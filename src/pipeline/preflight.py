"""Checks before the first paid call: ffmpeg, keys, voice catalog, cost estimate.

A clear error at the start instead of a crash mid-run. Key values are never printed, only names.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pipeline import pricing, voices
from pipeline.config import (
    MAX_FINAL_S,
    MAX_SCRIPT_FIX_ATTEMPTS,
    MAX_VOICE_ATTEMPTS,
    Pacing,
    Settings,
)
from pipeline.media import FfmpegError, ffmpeg_exe
from pipeline.nodes.voice import phrases
from pipeline.obs import Ledger
from pipeline.providers import Providers
from pipeline.schema import Brief

WORDS_PER_S = 2.5  # speech rate for estimating durations before synthesis


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimate_usd: float = 0.0
    worst_usd: float = 0.0
    max_run_usd: float = 0.0
    daily_left_usd: float = 0.0
    models: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems


def shot_estimate_s(line: str, p: Pacing | None = None) -> float:
    """Shot duration before synthesis: speech rate + configured pauses."""
    p = p or Pacing()
    pauses = sum(ph.pause_after_s for ph in phrases(line, p))
    return max(p.lead_s + len(line.split()) / WORDS_PER_S + pauses + p.tail_s, p.min_shot_s)


def estimate(brief: Brief, p: Providers, pacing: Pacing | None = None) -> tuple[float, float]:
    """(expected, worst) cost from the prices of the selected models."""
    allowed = p.video.spec.durations_s
    script = pricing.cost(p.script.spec)
    tts = sum(pricing.cost(p.tts.spec, chars=len(ln)) for ln in brief.lines)
    video = lipsync = 0.0
    for ln in brief.lines:
        shot_s = shot_estimate_s(ln, pacing)
        clip_s = next((d for d in allowed if d >= math.ceil(shot_s)), allowed[-1])
        video += pricing.cost(p.video.spec, seconds=clip_s)
        lipsync += pricing.cost(p.lipsync.spec, seconds=shot_s)
    expected = script + tts + video + lipsync
    worst = (
        script * (1 + MAX_SCRIPT_FIX_ATTEMPTS) + tts * (1 + MAX_VOICE_ATTEMPTS) + video + lipsync
    )
    return round(expected, 4), round(worst, 4)


def check(settings: Settings, providers: Providers, brief: Brief | None = None) -> Report:
    r = Report(max_run_usd=settings.max_run_cost_usd, models=providers.models())
    try:
        ffmpeg_exe()
    except FfmpegError as e:
        r.problems.append(str(e))

    needed: dict[str, list[str]] = {}
    for cap, prov in providers.by_capability().items():
        if prov.spec.key:
            needed.setdefault(prov.spec.key, []).append(f"{cap}={prov.name}")
    for key, users in needed.items():
        if not getattr(settings, key.lower(), ""):
            r.problems.append(f"{key} is not set in .env — needed for {', '.join(users)}")

    # Local models are the optional `local` extra.
    for cap, prov in providers.by_capability().items():
        if prov.spec.transport == "local":
            try:
                prov.ready()
            except ImportError as e:
                r.problems.append(f"{cap}={prov.name}: {e}")
    catalog = providers.tts.spec.voice_catalog
    missing = voices.missing(catalog)
    if missing:
        r.problems.append(
            f"voices.yaml: no {catalog} voices for {', '.join(missing)} "
            "— add voices to src/pipeline/voices.yaml"
        )
    if providers.tts.name == "kokoro":
        from pipeline.providers import kokoro

        if not kokoro.is_downloaded():
            r.warnings.append("Kokoro: the first run downloads the model (~120 MB) to ~/.cache")
    if brief is not None:
        lip = providers.lipsync.spec
        # Duration estimates from the speech rate are rough, so these are warnings; the exact
        # checks run after the voice, before paying for video (TooLong, OutOfModelLimits).
        total_s = sum(shot_estimate_s(ln, settings.pacing()) for ln in brief.lines)
        if total_s > MAX_FINAL_S:
            r.warnings.append(
                f"{len(brief.lines)} lines ≈ {total_s:.0f} s > {MAX_FINAL_S:.0f} s video limit: "
                "the run will likely stop after the voice, before paying for video — shorten lines"
            )
        # Lipsync gets only the shot (Kokoro speaks faster than the estimate: 28 words were a
        # 9.4 s shot, estimated at 13 s).
        for i, ln in enumerate(brief.lines, 1):
            shot_s = shot_estimate_s(ln, settings.pacing())
            if lip.input_s and not lip.input_s[0] <= shot_s <= lip.input_s[1]:
                r.warnings.append(
                    f"line {i}: shot ~{shot_s:.0f} s may be outside the lipsync model {lip.name} "
                    f"limits ({lip.input_s[0]:.0f}–{lip.input_s[1]:.0f} s); checked exactly after "
                    "the voice, before paying for video — shorten the line or choose another "
                    "LIPSYNC_MODEL"
                )

    ledger = Ledger(settings.runs_dir / "_ledger.jsonl")
    r.daily_left_usd = round(settings.max_daily_cost_usd - ledger.spent_today(), 4)
    if brief is not None:
        try:
            r.estimate_usd, r.worst_usd = estimate(brief, providers, settings.pacing())
        except voices.ConfigError as e:
            r.problems.append(str(e))
            return r
        cap = min(settings.max_run_cost_usd, r.daily_left_usd)
        if r.estimate_usd > cap:
            r.problems.append(
                f"expected cost ${r.estimate_usd:.2f} > available ${cap:.2f} "
                "(MAX_RUN_COST_USD / what is left of MAX_DAILY_COST_USD)"
            )
        elif r.worst_usd > cap:
            r.warnings.append(
                f"with all retries it may cost up to ${r.worst_usd:.2f} > ${cap:.2f}: "
                "the cap will stop the run earlier"
            )
    return r
