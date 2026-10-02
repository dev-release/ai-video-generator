"""Deterministic edit: shots + voice -> final.mp4, no models. Spec: specs/assemble.md."""

from __future__ import annotations

from pathlib import Path

from pipeline import cache
from pipeline.config import TARGET_LUFS
from pipeline.media import measure_lufs, probe, run_ffmpeg
from pipeline.nodes import Ctx, run_path
from pipeline.schema import FinalVideo, PipelineState
from pipeline.timeline import TooLong, ensure_shot_audio, timeline

__all__ = ["TooLong", "run", "timeline"]

W, H, FPS, SR = 1080, 1920, 30, 44100


def _filter(durations: list[float]) -> str:
    n = len(durations)
    parts = []
    for i, d in enumerate(durations):
        parts.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"fps={FPS},setsar=1,tpad=stop_mode=clone:stop_duration={d},"
            f"trim=duration={d},setpts=PTS-STARTPTS[v{i}]"
        )
        # Shot audio already has the lead and tail (timeline.ensure_shot_audio); only the format.
        parts.append(
            f"[{n + i}:a]aresample={SR},aformat=channel_layouts=mono,apad,"
            f"atrim=duration={d},asetpts=PTS-STARTPTS[a{i}]"
        )
    ins = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{ins}concat=n={n}:v=1:a=1[v][acat]")
    parts.append(f"[acat]loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11,aresample={SR}[a]")
    return ";".join(parts)


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    # Clips after lipsync (if any); the sound is only our shot audio.
    clips = {c.shot_id: c for c in (state.synced or state.clips)}
    pacing = ctx.settings.pacing()
    audio = ensure_shot_audio(state, pacing)
    voices = {v.shot_id: v for v in state.voices}
    order = [s.id for s in state.script.shots]
    durations = timeline([voices[i].duration_s for i in order], pacing)
    out = run_path(state.run_dir, "final.mp4")
    key = cache.input_hash(
        [cache.file_hash(Path(clips[i].path)) for i in order],
        [cache.file_hash(audio[i]) for i in order],
        durations,
    )
    if not cache.is_fresh(out, key):
        inputs = [a for i in order for a in ("-an", "-i", clips[i].path)]  # drop clip audio
        inputs += [a for i in order for a in ("-i", str(audio[i]))]
        run_ffmpeg(
            [
                *inputs,
                "-filter_complex", _filter(durations),
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-preset", "veryfast",
                "-c:a", "aac", "-ar", str(SR), "-b:a", "160k",
                "-movflags", "+faststart",
                str(out),
            ]
        )  # fmt: skip
        cache.mark(out, key)
    p = probe(out)
    final = FinalVideo(
        path=str(out),
        duration_s=round(p.duration_s, 3),
        width=p.width or 0,
        height=p.height or 0,
        lufs=round(measure_lufs(out), 2),
    )
    ctx.tracer.event("assemble", "final", **final.model_dump())
    return {"final": final}
