"""Verbatim gate: ASR of the FINAL file -> WER == 0. Spec: .claude/specs/verify.md."""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import jiwer

from pipeline.config import ASR_PAD_S
from pipeline.media import extract_audio_16k, write_atomic
from pipeline.nodes import Ctx, run_path
from pipeline.schema import AsrCheck, PipelineState, WordDiff
from pipeline.text import normalize


def word_diff(expected: str, heard: str) -> list[WordDiff]:
    a, b = expected.split(), heard.split()
    return [
        WordDiff(op=op, expected=" ".join(a[i1:i2]), heard=" ".join(b[j1:j2]))
        for op, i1, i2, j1, j2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
        if op != "equal"
    ]


def check(expected: str, heard: str, attempt: int, model: str) -> AsrCheck:
    exp_n, heard_n = normalize(expected), normalize(heard)
    # An empty transcript means silence in the final video: a failure, not "nothing to compare".
    wer = jiwer.wer(exp_n, heard_n) if heard_n else 1.0
    return AsrCheck(
        expected=expected,
        heard=heard,
        expected_norm=exp_n,
        heard_norm=heard_n,
        wer=round(wer, 4),
        passed=wer == 0,
        attempt=attempt,
        model=model,
        diff=word_diff(exp_n, heard_n),
    )


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script and state.final
    wav = extract_audio_16k(
        Path(state.final.path), run_path(state.run_dir, "asr_input.wav"), pad_s=ASR_PAD_S
    )
    heard = ctx.providers.asr.transcribe(wav)
    expected = " ".join(s.line for s in state.script.shots)
    result = check(expected, heard, state.verify_attempt, ctx.providers.asr.name)
    write_atomic(run_path(state.run_dir, "asr_check.json"), result.model_dump_json(indent=2))
    ctx.tracer.event("verify", "asr", wer=result.wer, passed=result.passed)
    if not result.passed:
        diff = "; ".join(f"{d.op}: '{d.expected}' → '{d.heard}'" for d in result.diff)
        ctx.tracer.info("verify", f"[red]WER {result.wer}[/] — {diff}")
    return {"asr": result, "verify_attempt": state.verify_attempt + 1}
