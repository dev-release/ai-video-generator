"""Lip/audio sync analysis: pure functions over series. Spec: .claude/specs/sync.md.

mouth — mouth openness per frame (0..1, None = no face); env — RMS envelope of OUR audio at the
same step. The code decides the verdict with thresholds from config, not a model.
"""

from __future__ import annotations

import numpy as np

from pipeline.config import (
    SYNC_FPS,
    SYNC_MAX_LAG_MS,
    SYNC_MIN_CORR,
    SYNC_MIN_FACE_RATIO,
    SYNC_MIN_MOUTH_RATIO,
    SYNC_OK_LAG_MS,
)
from pipeline.schema import ShotSync


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def best_lag(mouth: list[float | None], env: list[float], max_lag: int) -> tuple[int, float]:
    """Lag (frames) with the highest correlation. > 0 means the mouth is late."""
    n = min(len(mouth), len(env))
    j = np.array([np.nan if v is None else v for v in mouth[:n]], dtype=float)
    e = np.array(env[:n], dtype=float)
    best = (0, -1.0)
    for lag in range(-max_lag, max_lag + 1):
        # mouth at frame t matches audio at frame t - lag
        if lag >= 0:
            a, b = j[lag:], e[: n - lag]
        else:
            a, b = j[: n + lag], e[-lag:]
        ok = ~np.isnan(a)
        c = _pearson(a[ok], b[ok])
        if c > best[1]:
            best = (lag, c)
    return best


def speech_frames(
    start_s: float, end_s: float, pauses_s: list[tuple[float, float]], fps: int
) -> list[tuple[int, int]]:
    """Speech frames: [start, end) minus the pauses inside the line (the character is silent)."""
    out, at = [], start_s
    for a, b in sorted(pauses_s):
        if a > at:
            out.append((int(at * fps), int(min(a, end_s) * fps)))
        at = max(at, b)
    if end_s > at:
        out.append((int(at * fps), int(end_s * fps)))
    return out


def mouth_speech_ratio(mouth: list[float | None], speech: list[tuple[int, int]]) -> float | None:
    """Mean mouth openness in speech / in silence. A mouth moving in silence gives ~1 or less."""

    def in_speech(i: int) -> bool:
        return any(lo <= i < hi for lo, hi in speech)

    inside = [v for i, v in enumerate(mouth) if v is not None and in_speech(i)]
    outside = [v for i, v in enumerate(mouth) if v is not None and not in_speech(i)]
    if not inside or not outside:
        return None
    return round(float(np.mean(inside)) / max(float(np.mean(outside)), 1e-3), 3)


def analyze(
    shot_id: str, mouth: list[float | None], env: list[float], speech: list[tuple[int, int]]
) -> ShotSync:
    face_ratio = round(sum(v is not None for v in mouth) / max(len(mouth), 1), 3)
    if face_ratio < SYNC_MIN_FACE_RATIO:
        # Almost no face — a voice-over: nothing to sync, not a failure.
        return ShotSync(shot_id=shot_id, verdict="n/a", face_ratio=face_ratio, mouth=mouth, env=env)
    frame_ms = 1000 / SYNC_FPS
    lag, corr = best_lag(mouth, env, int(SYNC_MAX_LAG_MS / frame_ms))
    ratio = mouth_speech_ratio(mouth, speech)
    lag_ms = round(lag * frame_ms, 1)
    ok = (
        abs(lag_ms) <= SYNC_OK_LAG_MS
        and corr >= SYNC_MIN_CORR
        and (ratio is None or ratio >= SYNC_MIN_MOUTH_RATIO)
    )
    return ShotSync(
        shot_id=shot_id,
        verdict="ok" if ok else "off",
        face_ratio=face_ratio,
        best_lag_ms=lag_ms,
        corr=round(corr, 3),
        mouth_speech_ratio=ratio,
        mouth=mouth,
        env=env,
    )
