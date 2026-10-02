"""Lip sync check on the final video: mouth on screen vs our audio. Spec: .claude/specs/sync.md.

The code computes metrics and the verdict (sync.py); SYNC_GATE decides whether a fail stops the run.
"""

from __future__ import annotations

from pathlib import Path

from pipeline import sync
from pipeline.config import SYNC_FPS
from pipeline.media import audio_envelope, video_frames, write_atomic
from pipeline.nodes import Ctx, run_path
from pipeline.schema import PipelineState, SyncCheck
from pipeline.timeline import ensure_shot_audio, shot_durations


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.final
    face = ctx.providers.face
    pacing = ctx.settings.pacing()
    audio = ensure_shot_audio(state, pacing)
    voices = {v.shot_id: v for v in state.voices}
    shots, t = [], 0.0
    for sid, d in shot_durations(state, pacing).items():
        frames, w, h = video_frames(Path(state.final.path), t, d, SYNC_FPS)
        mouth = face.mouth(frames, w, h)
        env = audio_envelope(audio[sid], SYNC_FPS)
        v = voices[sid]
        pad = pacing.lead_s
        speech = sync.speech_frames(
            pad + (v.speech_start_s or 0.0),
            pad + (v.speech_end_s or v.duration_s),
            [(pad + a, pad + b) for a, b in v.pauses_s],
            SYNC_FPS,
        )
        shots.append(sync.analyze(sid, mouth, env, speech))
        t += d
    gate = ctx.settings.sync_gate
    off = [s.shot_id for s in shots if s.verdict == "off"]
    result = SyncCheck(passed=gate == "warn" or not off, gate=gate, shots=shots)
    write_atomic(run_path(state.run_dir, "sync_check.json"), result.model_dump_json(indent=2))
    ctx.tracer.event(
        "sync_check",
        "sync",
        verdicts={s.shot_id: s.verdict for s in shots},
        lag_ms={s.shot_id: s.best_lag_ms for s in shots},
        corr={s.shot_id: s.corr for s in shots},
    )
    if off:
        ctx.tracer.info("sync_check", f"[yellow]out of sync in {', '.join(off)}[/] (gate={gate})")
    return {"sync": result}
