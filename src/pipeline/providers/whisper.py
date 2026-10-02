"""Local faster-whisper. Independent of the TTS provider on purpose (spec verify.md)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pipeline.providers import SpecProvider, Timed, require_local
from pipeline.registry import ModelSpec


@lru_cache
def _model(name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(name, device="cpu", compute_type="int8")


class WhisperASR(SpecProvider):
    """Optional local ASR (extra `local`). Model size = spec.endpoint (e.g. small.en)."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        self.model = spec.endpoint or "small.en"

    def ready(self) -> None:
        require_local("faster_whisper")

    def _segments(self, wav: Path, words: bool):
        segments, _ = _model(self.model).transcribe(
            str(wav),
            language="en",
            temperature=0.0,
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=words,
        )
        return list(segments)

    def transcribe(self, wav: Path) -> str:
        return " ".join(s.text.strip() for s in self._segments(wav, False)).strip()

    def timed(self, wav: Path) -> Timed:
        segs = self._segments(wav, True)
        words = [w for s in segs for w in (s.words or [])]
        text = " ".join(s.text.strip() for s in segs).strip()
        if not words:
            return Timed(text, 0.0, 0.0)
        return Timed(text, round(float(words[0].start), 3), round(float(words[-1].end), 3))
