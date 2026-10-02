"""Audio gate BEFORE video: each shot wav -> ASR -> WER == 0. Spec: .claude/specs/sync.md.

A voice costs cents, a video costs dollars: a bad voice is caught here, not on the final file.
Also measures the speech window (from audio energy, by code) for the video prompt and sync check.
"""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.graph import END

from pipeline.config import MAX_VOICE_ATTEMPTS
from pipeline.media import speech_window, write_atomic
from pipeline.nodes import Ctx
from pipeline.nodes.verify import check
from pipeline.providers import Timed
from pipeline.schema import PipelineState, VoiceCheck


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    asr = ctx.providers.asr
    by_shot = {v.shot_id: v for v in state.voices}
    voices, checks = [], []
    retries = dict(state.voice_retries)
    for shot in state.script.shots:
        clip = by_shot[shot.id]
        # Only the text comes from ASR; speech bounds come from the audio (fal-whisper timestamps
        # can be [0.03, 30.01] on a 1.6 s phrase). str() normalizes types from any ASR.
        start, end = speech_window(Path(clip.path))
        heard = Timed(str(asr.transcribe(Path(clip.path))), start, end)
        r = check(shot.line, heard.text, retries.get(shot.id, 0), asr.name)
        vc = VoiceCheck(
            shot_id=shot.id,
            passed=r.passed,
            wer=r.wer,
            heard=heard.text,
            diff=r.diff,
            attempt=retries.get(shot.id, 0),
        )
        write_atomic(
            Path(clip.path).with_suffix(".check.json"),
            json.dumps(
                {
                    **vc.model_dump(),
                    "expected_norm": r.expected_norm,
                    "heard_norm": r.heard_norm,
                    "speech": heard[1:],
                },
                ensure_ascii=False,
                indent=2,
            ),  # fmt: skip
        )
        checks.append(vc)
        voices.append(
            clip.model_copy(
                update={"speech_start_s": heard.speech_start_s, "speech_end_s": heard.speech_end_s}
            )
        )
        if not r.passed:
            # Regenerate only the failed shot: a new seed for it alone.
            retries[shot.id] = retries.get(shot.id, 0) + 1
            diff = "; ".join(f"'{d.expected}' → '{d.heard}'" for d in r.diff)
            ctx.tracer.info("voice_check", f"[red]{shot.id} WER {r.wer}[/] — {diff}")
    failed = [c.shot_id for c in checks if not c.passed]
    ctx.tracer.event("voice_check", "asr", failed=failed, wer={c.shot_id: c.wer for c in checks})
    patch: dict = {"voices": voices, "voice_checks": checks, "voice_retries": retries}
    if failed:
        patch["voice_attempt"] = state.voice_attempt + 1
    return patch


def route(state: PipelineState) -> str:
    if all(c.passed for c in state.voice_checks):
        return "approve_script"
    return "voice" if state.voice_attempt <= MAX_VOICE_ATTEMPTS else END
