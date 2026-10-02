"""Local Kokoro-82M voice (Apache-2.0) via kokoro-onnx.

No network after the first run, any OS (espeak-ng comes as a pip package), $0. It does not
understand emotion tags and gets the line as is.
"""

from __future__ import annotations

import re
import wave
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np

from pipeline.media import run_ffmpeg
from pipeline.obs import console
from pipeline.providers import SpecProvider, require_local
from pipeline.registry import ModelSpec
from pipeline.schema import CastEntry

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
FILES = {"kokoro-v1.0.int8.onnx": 80_000_000, "voices-v1.0.bin": 20_000_000}
CACHE_DIR = Path.home() / ".cache" / "pipeline" / "kokoro"


def is_downloaded(cache_dir: Path = CACHE_DIR) -> bool:
    return all(
        (cache_dir / f).exists() and (cache_dir / f).stat().st_size >= n for f, n in FILES.items()
    )


def download(cache_dir: Path = CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, min_size in FILES.items():
        dst = cache_dir / name
        if dst.exists() and dst.stat().st_size >= min_size:
            continue
        console.print(f"  [dim]kokoro: downloading {name} (once) -> {cache_dir}[/]")
        tmp = dst.with_suffix(".part")
        with httpx.stream("GET", f"{_RELEASE}/{name}", follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        if tmp.stat().st_size < min_size:
            tmp.unlink()
            raise RuntimeError(f"kokoro: {name} downloaded incompletely")
        tmp.rename(dst)


@lru_cache
def _engine(cache_dir: Path):
    from kokoro_onnx import Kokoro

    return Kokoro(str(cache_dir / "kokoro-v1.0.int8.onnx"), str(cache_dir / "voices-v1.0.bin"))


# Kokoro's phonemizer (espeak, preserve_punctuation) reads a decimal point as a sentence end when
# the text ends with a period: "in 3.5 minutes." -> "three. five" (without the final period:
# "three point five"). So drop only the final period and only when there is a decimal; the
# words of the request do not change.
_DECIMAL = re.compile(r"\d\.\d")


def spoken_text(text: str) -> str:
    if text.endswith(".") and not text.endswith("..") and _DECIMAL.search(text):
        return text[:-1]
    return text


class KokoroTTS(SpecProvider):
    def __init__(self, spec: ModelSpec, cache_dir: Path = CACHE_DIR) -> None:
        self.spec = spec
        self.cache_dir = cache_dir

    def ready(self) -> None:
        require_local("kokoro_onnx")

    def speak(self, text: str, voice: CastEntry, seed: int, out: Path) -> Path:
        # Kokoro is deterministic; the seed is unused but the protocol is shared.
        if not is_downloaded(self.cache_dir):
            download(self.cache_dir)
        audio, sr = _engine(self.cache_dir).create(
            spoken_text(text), voice=voice.voice_id, lang="en-us"
        )
        raw = out.with_suffix(".raw.wav")
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
        run_ffmpeg(["-i", str(raw), "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", str(out)])
        raw.unlink()
        return out
