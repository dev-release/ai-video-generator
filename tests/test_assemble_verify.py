"""Specs: assemble.md, verify.md. The node chain on fake (tone + EchoASR), no models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.config import MAX_FINAL_S, TARGET_LUFS, Pacing
from pipeline.media import probe
from pipeline.nodes import (
    approve,
    approve_script,
    assemble,
    keyframe,
    script,
    verify,
    video,
    voice,
)
from tests.conftest import LINE_1, LINE_2


def run_chain(state, ctx, until: str = "verify"):
    # Video plan approval (auto) -> shot 1 from text -> cut frame -> approval (auto) -> shot 2 from
    # the frame -> edit (spec approve_script.md, keyframe.md).
    chain = (script, voice, approve_script, video, keyframe, approve, video, assemble, verify)
    for node in chain:
        state = state.model_copy(update=node.run(state, ctx))
        if node.__name__.endswith(until):
            break
    return state


@pytest.fixture
def assembled(make_ctx, make_state):
    ctx = make_ctx()
    return run_chain(make_state(), ctx, until="assemble"), ctx


def test_final_format(assembled):
    state, _ = assembled
    p = probe(Path(state.final.path))
    assert (p.width, p.height) == (1080, 1920)
    assert p.has_video and p.has_audio
    assert 0 < p.duration_s <= MAX_FINAL_S


def test_loudness_near_target(assembled):
    state, _ = assembled
    assert abs(state.final.lufs - TARGET_LUFS) <= 1.0


def test_short_final_is_short_no_silent_tail(make_ctx, make_state):
    # The video is the lines plus short pauses, not padded to 8 s.
    state = run_chain(make_state(lines=("I never wrote this.",)), make_ctx(), until="assemble")
    p = Pacing()
    shot = max(p.lead_s + state.voices[0].duration_s + p.tail_s, p.min_shot_s)
    assert abs(probe(Path(state.final.path)).duration_s - shot) < 0.1


def test_timeline_rejects_too_long():
    with pytest.raises(assemble.TooLong):
        assemble.timeline([20.0, 12.0])


def test_verify_passes_on_exact_speech(assembled):
    state, ctx = assembled
    state = state.model_copy(update=verify.run(state, ctx))
    assert state.asr.passed and state.asr.wer == 0
    assert state.asr.expected == f"{LINE_1} {LINE_2}"
    assert state.verify_attempt == 1
    on_disk = json.loads(Path(state.run_dir, "asr_check.json").read_text())
    assert on_disk["passed"] is True


def test_verify_fails_on_missing_word(assembled):
    state, ctx = assembled
    sidecar = Path(state.run_dir, "voice", "s2.txt")
    sidecar.write_text(LINE_2.replace(" 3 days", " days"))  # a "swallowed" number
    result = verify.run(state, ctx)["asr"]
    assert not result.passed and result.wer > 0
    assert any(d.op == "delete" and d.expected == "three" for d in result.diff)


def test_verify_fails_on_silence(assembled):
    state, ctx = assembled
    for p in Path(state.run_dir, "voice").glob("*.txt"):
        p.write_text("")
    assert verify.run(state, ctx)["asr"].passed is False


def test_spoken_tag_is_a_real_mismatch():
    r = verify.check("I never wrote this.", "whispers I never wrote this", 0, "t")
    assert not r.passed


def test_line_invariant_end_to_end(make_ctx, make_state):
    # script.json == the TTS request text == the verify reference.
    state = run_chain(make_state(), make_ctx())
    lines = [s["line"] for s in json.loads(Path(state.run_dir, "script.json").read_text())["shots"]]
    requests = [
        json.loads(Path(state.run_dir, "voice", f"{sid}.request.json").read_text())
        for sid in ("s1", "s2")
    ]
    assert lines == [LINE_1, LINE_2]
    assert [r["tts_text"] for r in requests] == lines
    assert state.asr.expected == " ".join(lines)


def test_asr_copy_has_silence_around_but_the_video_does_not(assembled):
    # Whisper drops the first word at the very start of a file: ASR hears a copy with ASR_PAD_S
    # of silence around it; the video is unchanged (spec verify.md).
    from pipeline.config import ASR_PAD_S

    state, ctx = assembled
    verify.run(state, ctx)
    asr_in = probe(Path(state.run_dir, "asr_input.wav")).duration_s
    final = probe(Path(state.final.path)).duration_s
    assert abs(asr_in - (final + 2 * ASR_PAD_S)) < 0.1
