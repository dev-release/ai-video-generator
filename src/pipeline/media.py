"""ffmpeg wrappers. The binary comes from imageio-ffmpeg — no system dependency."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


@lru_cache
def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as e:
        raise FfmpegError(
            "ffmpeg not found. Run `uv sync` (imageio-ffmpeg package) or install a system ffmpeg."
        ) from e


@contextmanager
def atomic(path: Path) -> Iterator[Path]:
    """Write to a temp file and swap it in atomically: a reader (UI, next stage) sees either the
    old complete file or the new complete one, never a partial file."""
    path = Path(path)
    tmp_dir = path.parent / ".tmp"  # separate dir: glob("*.wav") in the work dir does not see it
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / path.name  # same extension: ffmpeg picks the format from it
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_atomic(path: Path, data: str | bytes) -> Path:
    with atomic(path) as tmp:
        tmp.write_bytes(data.encode() if isinstance(data, str) else data)
    return Path(path)


def run_ffmpeg(args: list[str]) -> str:
    out = args[-1]
    if out == "-" or out.startswith("-"):  # output to a pipe/null, no atomic write needed
        return _ffmpeg(args)
    with atomic(Path(out)) as tmp:
        return _ffmpeg([*args[:-1], str(tmp)])


def _ffmpeg(args: list[str]) -> str:
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise FfmpegError(f"ffmpeg exit {proc.returncode}:\n{tail}")
    return proc.stderr


@dataclass(frozen=True)
class Probe:
    duration_s: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool


def probe(path: Path) -> Probe:
    # imageio-ffmpeg ships no ffprobe, so parse the `ffmpeg -i` banner.
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    err = proc.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", err)
    video = re.search(r"Stream #.*Video:.*?, (\d{2,5})x(\d{2,5})", err)
    if not m and not video:
        raise FfmpegError(f"not readable as media: {path}")
    # An image (cut frame) has no duration ("Duration: N/A"); that is not an error.
    h, mi, s = m.groups() if m else ("0", "0", "0")
    return Probe(
        duration_s=int(h) * 3600 + int(mi) * 60 + float(s),
        width=int(video.group(1)) if video else None,
        height=int(video.group(2)) if video else None,
        has_video=video is not None,
        has_audio=re.search(r"Stream #.*Audio:", err) is not None,
    )


def measure_lufs(path: Path) -> float:
    out = run_ffmpeg(["-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    blob = out[out.rindex("{") : out.rindex("}") + 1]
    return float(json.loads(blob)["input_i"])


def extract_audio_16k(src: Path, dst: Path, pad_s: float = 0.0) -> Path:
    """Audio for ASR: mono 16 kHz with pad_s of silence around — a COPY for analysis."""
    pad = f"adelay={int(pad_s * 1000)}:all=1,apad=pad_dur={pad_s}" if pad_s else "anull"
    run_ffmpeg(["-i", str(src), "-vn", "-af", pad, "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(dst)])  # fmt: skip
    return dst


def shot_audio(voice_wav: Path, pad_before_s: float, duration_s: float, out: Path) -> Path:
    """Shot audio: silence + the line wav + silence up to the shot duration. The same file goes
    to lipsync and to the final video, so the lips are fitted to exactly what is heard."""
    run_ffmpeg(
        [
            "-i", str(voice_wav),
            "-af", f"aresample=44100,aformat=channel_layouts=mono,"
                   f"adelay={int(pad_before_s * 1000)}:all=1,apad,atrim=duration={duration_s}",
            "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(out),
        ]
    )  # fmt: skip
    return out


def video_frames(src: Path, start_s: float, dur_s: float, fps: int, width: int = 360):
    """Frames of a segment as raw RGB bytes (downscaled for speed). -> (frames, w, h)."""
    height = int(width * 16 / 9)
    raw = subprocess.run(
        [
            ffmpeg_exe(), "-hide_banner", "-nostdin", "-ss", f"{start_s}", "-t", f"{dur_s}",
            "-i", str(src), "-vf", f"fps={fps},scale={width}:{height}",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    size = width * height * 3
    return [raw[i : i + size] for i in range(0, len(raw) - size + 1, size)], width, height


def extract_frame(src: Path, at_s: float, out: Path) -> Path:
    """One video frame at at_s -> png (accurate seek: -ss after -i)."""
    run_ffmpeg(["-i", str(src), "-ss", f"{at_s:.3f}", "-frames:v", "1", str(out)])
    return out


def read_frames(src: Path, fps: int):
    """Video frames as RGB arrays (h, w, 3), one by one: a 1080x1920 clip does not fit in memory."""
    import numpy as np

    p = probe(src)
    if not (p.width and p.height):
        raise FfmpegError(f"no video track: {src}")
    w, h = p.width, p.height
    proc = subprocess.Popen(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(src),
         "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    assert proc.stdout
    size = w * h * 3
    try:
        while len(buf := proc.stdout.read(size)) == size:
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).copy()
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def write_frames(frames, width: int, height: int, fps: int, out: Path) -> Path:
    """RGB frames -> silent mp4 (h264, yuv420p), written atomically."""
    import tempfile

    with atomic(Path(out)) as tmp, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
             "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
             str(tmp)],
            stdin=subprocess.PIPE,
            stderr=err,
        )  # fmt: skip
        assert proc.stdin
        try:
            for f in frames:
                proc.stdin.write(f.tobytes())
        finally:
            proc.stdin.close()
            code = proc.wait()
        if code != 0:
            err.seek(0)
            raise FfmpegError(f"ffmpeg exit {code}:\n{err.read().decode(errors='replace')[-800:]}")
    return Path(out)


def read_pcm(src: Path, sr: int):
    """Audio as mono float32 [-1, 1] at rate sr."""
    import numpy as np

    raw = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def write_wav(out: Path, x, sr: int) -> Path:
    """mono float [-1, 1] -> pcm_s16le wav, written atomically."""
    import io
    import wave

    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return write_atomic(out, buf.getvalue())


def audio_envelope(wav: Path, fps: int) -> list[float]:
    """RMS envelope with a 1/fps step, normalized to 0..1."""
    import numpy as np

    raw = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(wav), "-ac", "1", "-ar", "16000",
         "-f", "s16le", "-"],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    hop = 16000 // fps
    n = len(x) // hop
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.zeros(0)
    peak = float(rms.max()) if n else 0.0
    return [round(float(v / peak), 4) if peak else 0.0 for v in rms]


def silent_runs(src: Path, min_s: float = 0.15, fps: int = 100) -> list[tuple[float, float]]:
    """Silent spans (>= min_s) in a file, in seconds: envelope < 2% of peak. Measures dead time
    in tests and evals."""
    env = audio_envelope(src, fps)
    out, start = [], None
    for i, quiet in enumerate([v < 0.02 for v in env] + [False]):
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if i - start >= min_s * fps:
                out.append((round(start / fps, 2), round(i / fps, 2)))
            start = None
    return out


def speech_window(src: Path, fps: int = 100) -> tuple[float, float]:
    """Speech bounds in a file (s): first and last span with energy >= 5% of peak. Computed from
    the audio because ASR timestamps can be broken (spec sync.md)."""
    env = audio_envelope(src, fps)
    loud = [i for i, v in enumerate(env) if v >= 0.05]
    if not loud:
        return 0.0, 0.0
    return round(loud[0] / fps, 3), round((loud[-1] + 1) / fps, 3)


def trim_video(src: Path, duration_s: float, out: Path) -> Path:
    """The first duration_s seconds of a video, no audio. Re-encoded: `-c copy` can only cut on
    keyframes."""
    run_ffmpeg(["-i", str(src), "-t", f"{duration_s:.3f}", "-an", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", str(out)])  # fmt: skip
    return out


def trim_audio(src: Path, duration_s: float, out: Path) -> Path:
    """Audio of exactly duration_s (cut or padded with silence) to match a clip."""
    run_ffmpeg(["-i", str(src), "-af", f"apad,atrim=duration={duration_s}",
                "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(out)])  # fmt: skip
    return out
