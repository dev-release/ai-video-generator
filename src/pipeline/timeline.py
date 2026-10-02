"""Shot timeline and shot audio, shared by video, lipsync, assemble and sync_check.

One duration computation means the lips are fitted to exactly the audio of the final video.
Pacing is configuration (`config.Pacing`), not code. Spec: assemble.md.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import cache
from pipeline.config import MAX_FINAL_S, Pacing
from pipeline.media import shot_audio
from pipeline.nodes import run_path
from pipeline.schema import PipelineState


class TooLong(RuntimeError):
    pass


def timeline(voice_s: list[float], p: Pacing | None = None) -> list[float]:
    """Each shot: a short lead pause + the line + a tail pause, at least min_shot. Padding with
    silence up to min_final happens only when configured (off by default)."""
    p = p or Pacing()
    d = [round(max(p.lead_s + v + p.tail_s, p.min_shot_s), 3) for v in voice_s]
    total = sum(d)
    if total > MAX_FINAL_S:
        # The script should have prevented this; cutting the line to fit is not allowed.
        raise TooLong(f"video {total:.1f}s > {MAX_FINAL_S}s — the lines are too long")
    d[-1] = round(d[-1] + max(0.0, p.min_final_s - total), 3)
    return d


def shot_durations(state: PipelineState, p: Pacing) -> dict[str, float]:
    assert state.script
    voices = {v.shot_id: v for v in state.voices}
    order = [s.id for s in state.script.shots]
    return dict(zip(order, timeline([voices[i].duration_s for i in order], p), strict=True))


def ensure_shot_audio(state: PipelineState, p: Pacing) -> dict[str, Path]:
    """Audio of each shot: lead silence + wav + silence up to the shot duration. Idempotent."""
    voices = {v.shot_id: v for v in state.voices}
    out = {}
    for sid, d in shot_durations(state, p).items():
        path = run_path(state.run_dir, "voice", f"{sid}.shot.wav")
        key = cache.input_hash(cache.file_hash(Path(voices[sid].path)), p.lead_s, d)
        if not cache.is_fresh(path, key):
            shot_audio(Path(voices[sid].path), p.lead_s, d, path)
            cache.mark(path, key)
        out[sid] = path
    return out
