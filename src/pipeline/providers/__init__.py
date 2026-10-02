"""Capability protocols and building providers from the model registry (spec: providers.md).

Nodes know only the capability protocol. Which model sits behind it is a models.yaml entry chosen
by config; the adapter is picked by (transport, capability) — one per pair, never per model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, get_args

from pipeline import registry
from pipeline.registry import ModelSpec
from pipeline.schema import Brief, CastEntry, ScriptDraft

if TYPE_CHECKING:
    from pipeline.config import Settings


class ContentRejected(RuntimeError):
    """The provider's content checker refused the request (fal `content_policy_violation`,
    not retryable). The same request is refused again, so resuming does not help: a new take
    or a rewritten script does. Billing is unknown — fal: a 422 "may still be charged"."""

    content_rejected = True


class Provider(Protocol):
    spec: ModelSpec

    @property
    def name(self) -> str: ...


class ScriptWriter(Provider, Protocol):
    def write(self, brief: Brief, feedback: str | None) -> ScriptDraft: ...


class VideoGen(Provider, Protocol):
    """Silent shot clip: image=None starts from text, otherwise from the given start frame.
    seed is the take number; models without a seed get a new request per take instead."""

    def animate(
        self, image: Path | None, motion: str, duration_s: float, out: Path, seed: int = 0
    ) -> Path: ...


class TTS(Provider, Protocol):
    def speak(self, text: str, voice: CastEntry, seed: int, out: Path) -> Path: ...


class Timed(NamedTuple):
    text: str
    speech_start_s: float
    speech_end_s: float


class ASR(Provider, Protocol):
    def transcribe(self, wav: Path) -> str: ...

    def timed(self, wav: Path) -> Timed: ...


class LipSync(Provider, Protocol):
    """Moves the lips in a clip to OUR shot audio; only the video track of the result is used."""

    def sync(self, clip: Path, shot_audio: Path, out: Path, seed: int = 0) -> Path: ...


class FaceTracker(Provider, Protocol):
    """Mouth openness (0..1) per frame; None where there is no face."""

    def mouth(self, frames: list[bytes], width: int, height: int) -> list[float | None]: ...

    def has_face(self, image: Path) -> bool | None:
        """None means unknown (no tracker), and then nothing is skipped."""
        ...


class SpecProvider:
    """Adapter base: the provider name is the registry entry name."""

    spec: ModelSpec

    @property
    def name(self) -> str:
        return self.spec.name

    def ready(self) -> None:
        """Fail before spending (missing key, missing local package)."""


@dataclass(frozen=True)
class Providers:
    script: ScriptWriter
    video: VideoGen
    tts: TTS
    asr: ASR
    lipsync: LipSync = field(default_factory=lambda: _fake().PassthroughLipSync())
    face: FaceTracker = field(default_factory=lambda: _fake().NoFaceTracker())

    def by_capability(self) -> dict[str, Provider]:
        return {
            "script": self.script,
            "tts": self.tts,
            "video": self.video,
            "lipsync": self.lipsync,
            "asr": self.asr,
            "face": self.face,
        }

    def models(self) -> dict[str, str]:
        return {cap: p.name for cap, p in self.by_capability().items()}


def _fake():
    from pipeline.providers import fake

    return fake


@dataclass
class Deps:
    """Shared adapter dependencies (one fal client per run)."""

    settings: Settings
    _fal: object | None = None

    @property
    def fal(self):
        from pipeline.providers.fal import FalQueue

        if self._fal is None:
            self._fal = FalQueue(self.settings)
        return self._fal


def _adapters() -> dict[tuple[str, str], Callable[[ModelSpec, Deps], Provider]]:
    from pipeline.providers import claude, face, fake, fal

    # Local models are built without importing heavy packages: ready() checks they are installed,
    # so preflight says "make install-local" instead of crashing here.
    def local_tts(spec, _d):
        from pipeline.providers.kokoro import KokoroTTS

        return KokoroTTS(spec)

    def local_asr(spec, _d):
        from pipeline.providers.whisper import WhisperASR

        return WhisperASR(spec)

    return {
        ("anthropic", "script"): lambda s, d: claude.ClaudeScriptWriter(s, d.settings),
        ("fal", "video"): lambda s, d: fal.FalVideo(s, d.fal),
        ("fal", "lipsync"): lambda s, d: fal.FalLipSync(s, d.fal),
        ("fal", "asr"): lambda s, d: fal.FalASR(s, d.fal),
        **{("fake", cap): lambda s, d: fake.fixture(s) for cap in get_args(registry.Capability)},
        ("local", "tts"): local_tts,
        ("local", "asr"): local_asr,
        ("local", "face"): lambda s, d: face.YuNetMouth(s),
    }


def require_local(module: str) -> None:
    """Checks the package without importing it: faster-whisper pulls PyAV, which next to OpenCV
    loads a second copy of libavdevice into the process."""
    import importlib.util

    if importlib.util.find_spec(module) is None:
        raise ImportError(f"local model ({module}) is not installed: make install-local")


def make(spec: ModelSpec, deps: Deps) -> Provider:
    factory = _adapters().get((spec.transport, spec.capability))
    if factory is None:
        raise registry.ConfigError(
            f"no adapter for transport {spec.transport!r} and capability {spec.capability!r}"
        )
    return factory(spec, deps)


def build(settings: Settings) -> Providers:
    """Providers for the models selected in config — the same path for fake and real."""
    deps = Deps(settings)
    built = {cap: make(registry.get(cap, name), deps) for cap, name in settings.selected().items()}
    return Providers(**built)
