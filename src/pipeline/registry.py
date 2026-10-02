"""Model registry: one schema for every model (spec: .claude/specs/providers.md).

An entry says how to call the model (transport, endpoint, args template, result path) plus the
rules that apply to all models alike (sourced price, key, timeout, limits).
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.voices import ConfigError

CATALOG = Path(__file__).with_name("models.yaml")
Capability = Literal["script", "tts", "video", "lipsync", "asr", "face"]
Transport = Literal["fal", "anthropic", "fake", "local"]
NETWORK = {"fal", "anthropic"}


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit: Literal["call", "second", "1k_chars"]
    usd: float = Field(ge=0)
    bill_step: float = Field(default=1, gt=0)  # units are billed rounded up (Kling LipSync: 5 s)
    # Per-1M-token prices: when the provider reports usage, the actual cost replaces the estimate.
    in_per_mtok: float | None = Field(default=None, ge=0)
    out_per_mtok: float | None = Field(default=None, ge=0)
    source: str | None = None
    checked: date | None = None


class Words(BaseModel):
    """Where the ASR response keeps word timestamps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    start: str
    end: str


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    capability: Capability
    transport: Transport
    endpoint: str | None = None
    args: dict[str, Any] = {}
    reference_endpoint: str | None = None
    reference_args: dict[str, Any] = {}
    output: str | None = None
    words: Words | None = None
    key: str | None = None
    timeout_s: float = 60
    price: Price
    durations_s: list[float] = []
    # [min, max] input media duration (Kling LipSync: 2–10 s), checked before the paid step that
    # produces that media.
    input_s: list[float] = []
    native_audio: bool = False
    audio_off: dict[str, Any] = {}
    max_chars: int | None = None
    voice_catalog: str | None = None
    note: str | None = None  # shown in the UI next to the model

    @model_validator(mode="after")
    def _same_rules_for_all(self) -> ModelSpec:
        where = f"models.yaml {self.capability}.{self.name}"
        if self.transport in NETWORK:
            # A paid model without a sourced price would slip past the cost ceiling.
            if not (self.price.source and self.price.checked):
                raise ValueError(f"{where}: price needs source and checked date")
            if not (self.key and self.endpoint):
                raise ValueError(f"{where}: key and endpoint are required")
        if self.transport == "fal" and not self.output:
            raise ValueError(f"{where}: output (result path in the response) is required")
        if self.input_s and (len(self.input_s) != 2 or self.input_s[0] > self.input_s[1]):
            raise ValueError(f"{where}: input_s must be [min, max]")
        if self.capability == "video" and not self.durations_s:
            raise ValueError(f"{where}: video needs durations_s — the code picks the duration")
        if self.capability == "video" and self.transport == "fal" and not self.reference_endpoint:
            raise ValueError(f"{where}: video needs reference_endpoint (image-to-video)")
        # The video model's own audio is never used: the words come only from our wav.
        off = self.audio_off
        for name, tpl in (("args", self.args), ("reference_args", self.reference_args)):
            if name == "reference_args" and not self.reference_endpoint:
                continue
            if self.native_audio and (not off or any(tpl.get(k) != v for k, v in off.items())):
                raise ValueError(f"{where}: native_audio — {name} must contain audio_off")
        return self


@lru_cache
def catalog(path: Path = CATALOG) -> dict[tuple[str, str], ModelSpec]:
    raw = yaml.safe_load(path.read_text()) or {}
    out = {}
    for cap, models in raw.items():
        for name, body in (models or {}).items():
            out[(cap, name)] = ModelSpec(name=name, capability=cap, **body)
    return out


def get(capability: str, name: str) -> ModelSpec:
    try:
        return catalog()[(capability, name)]
    except KeyError:
        known = sorted(n for c, n in catalog() if c == capability)
        raise ConfigError(
            f"model {capability}.{name!r} is not in models.yaml; known: {', '.join(known)}"
        ) from None


def names(capability: str) -> list[str]:
    return sorted(n for c, n in catalog() if c == capability)


_VAR = re.compile(r"\{(\w+)\}")


def render(template: Any, **vars: Any) -> Any:
    """Fill an args template. A whole "{x}" keeps the value's type; anything else is formatted."""
    if isinstance(template, dict):
        return {k: render(v, **vars) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, **vars) for v in template]
    if isinstance(template, str):
        missing = [v for v in _VAR.findall(template) if v not in vars]
        if missing:
            raise ConfigError(f"args template: unknown variables {missing}")
        whole = _VAR.fullmatch(template)
        return vars[whole.group(1)] if whole else template.format(**vars)
    return template


def extract(obj: Any, path: str) -> Any:
    """Value at a dotted path like "video.url" in a model response."""
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj
