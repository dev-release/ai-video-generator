"""Pauses in speech: the code splits the line into phrases with silence between. Spec: voice.md."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.config import Pacing, Settings
from pipeline.graph import Runner
from pipeline.media import probe, silent_runs
from pipeline.nodes.video import motion_prompt
from pipeline.nodes.voice import join_phrases, phrases
from pipeline.providers import fake
from pipeline.schema import Brief, CastEntry, Delivery, Shot, VoiceClip, VoiceProfile
from pipeline.text import normalize

P = Pacing()  # default pacing
VOICE = CastEntry(profile=VoiceProfile.female_young_warm, provider="none", voice_id="x", seed=1)


@pytest.mark.parametrize(
    ("line", "texts", "pauses"),
    [
        ("This handwriting is mine. But I never wrote this letter.",
         ["This handwriting is mine.", "But I never wrote this letter."], [P.pause_after(".")]),
        ("Wait... this handwriting is mine.", ["Wait...", "this handwriting is mine."],
         [P.pause_after("...")]),
        ("Wait… it is mine.", ["Wait…", "it is mine."], [P.pause_after("...")]),
        ("I know who wrote it — it was me, years from now!",
         ["I know who wrote it —", "it was me, years from now!"], [P.pause_after("—")]),
        ("Stop! Who are you? Tell me now.", ["Stop!", "Who are you?", "Tell me now."],
         [P.pause_after("!"), P.pause_after("?")]),
        ("One thing: never come back.", ["One thing:", "never come back."], [P.pause_after(":")]),
        ("Wow, what a beautiful wood", ["Wow, what a beautiful wood"], []),
        ("It costs 3.50 dollars, Dr. Smith said.", ["It costs 3.50 dollars, Dr. Smith said."], []),
        ("I have 21st-century problems.", ["I have 21st-century problems."], []),
    ],
)  # fmt: skip
def test_line_is_split_into_phrases_at_pause_marks(line, texts, pauses):
    parts = phrases(line)
    assert [p.text for p in parts] == texts
    assert [p.pause_after_s for p in parts[:-1]] == pauses and parts[-1].pause_after_s == 0
    # Phrases are substrings of the line in order; no word is lost or added.
    at = 0
    for p in parts:
        at = line.index(p.text, at) + len(p.text)
    assert normalize(" ".join(p.text for p in parts)) == normalize(line)


def _say(tmp_path: Path, text: str, name: str) -> Path:
    return fake.ToneTTS().speak(text, VOICE, 1, tmp_path / f"{name}.wav")


def test_join_puts_exactly_the_configured_pause_between_phrases(tmp_path):
    a, b = _say(tmp_path, "Wait...", "a"), _say(tmp_path, "this handwriting is mine.", "b")
    out = tmp_path / "s1.wav"
    pauses = join_phrases([a, b], [1.0], out)
    assert len(pauses) == 1
    start, end = pauses[0]
    assert abs((end - start) - 1.0) < 0.01
    # TTS silence at phrase edges is trimmed: the only pause is the one the code set.
    assert probe(out).duration_s < probe(a).duration_s + 1.0 + probe(b).duration_s
    # Silence in the file exactly where the code put it.
    found = [r for r in silent_runs(out) if r[1] - r[0] > 0.8]
    assert len(found) == 1 and abs(found[0][0] - start) < 0.05 and abs(found[0][1] - end) < 0.05


def test_motion_prompt_tells_where_the_character_is_silent():
    shot = Shot(id="s1", character_id="maya", line="Wait... this is mine.",
                delivery=Delivery.neutral, visual_prompt="kitchen")  # fmt: skip
    v = VoiceClip(shot_id="s1", path="x", duration_s=3.0, text=shot.line,
                  speech_start_s=0.0, speech_end_s=2.6, pauses_s=[(0.4, 1.4)])  # fmt: skip
    prompt = motion_prompt(shot, v)
    lo, hi = P.lead_s + 0.4, P.lead_s + 1.4
    assert "silent" in prompt and "mouth closed" in prompt
    assert f"from {np.ceil(lo * 2) / 2:.1f}s to {np.floor(hi * 2) / 2:.1f}s" in prompt


def test_run_with_pauses_keeps_line_and_closes_mouth_in_the_pause(tmp_path):
    s = Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )
    line = "Wait... this handwriting is mine. But I never wrote it."
    r = Runner(s).start(Brief(idea="A girl finds a letter", lines=[line]))
    assert r.status == "done", r.error
    assert r.state and r.state.script and r.state.script.shots[0].line == line  # №1
    v = r.state.voices[0]
    assert len(v.pauses_s) == 2
    req = json.loads(Path(r.state.run_dir, "voice", "s1.request.json").read_text())
    assert [p["text"] for p in req["parts"]] == [p.text for p in phrases(line)]
    assert "⏸" in req["tts_text"]
    assert r.state.asr and r.state.asr.passed  # verbatim on the joined audio
    shot = r.state.sync.shots[0] if r.state.sync else None
    assert shot and shot.verdict == "ok", shot
    # The mouth is closed during a pause: openness is near its minimum mid-pause.
    for a, b in v.pauses_s:
        lo, hi = (round((P.lead_s + t) * 30) for t in (a + 0.15, b - 0.15))
        mid = [m for m in shot.mouth[lo:hi] if m is not None]
        assert mid and max(mid) < 0.1, (a, b, mid)


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("We will land in 3.5 minutes.", "We will land in 3.5 minutes"),
        ("It costs $2.50 now.", "It costs $2.50 now"),
        ("We will land in 3.5 minutes!", "We will land in 3.5 minutes!"),
        ("Please stay seated.", "Please stay seated."),
        ("Wait... 3.5 left...", "Wait... 3.5 left..."),
    ],
)
def test_kokoro_reads_decimals_at_sentence_end(text, spoken):
    # A phrase ending with a period is now common (pauses); Kokoro read "3.5." as "three. five".
    from pipeline.providers.kokoro import spoken_text

    assert spoken_text(text) == spoken


def test_speech_window_comes_from_the_sound_not_broken_asr_timestamps(make_ctx, make_state):
    """Live smoke of fal-whisper: words of a 1.6 s phrase came with timestamps [0.03, 30.01].
    The code computes the speech window from audio energy; only the text comes from ASR."""
    from pipeline.nodes import script, voice, voice_check
    from pipeline.providers import Timed

    class BrokenTimestamps(fake.EchoASR):
        def timed(self, wav):
            return Timed(self.transcribe(wav), 0.03, 30.01)

    ctx = make_ctx(asr=BrokenTimestamps())
    state = make_state(lines=("I never wrote this letter.",))
    for node in (script, voice, voice_check):
        state = state.model_copy(update=node.run(state, ctx))
    v = state.voices[0]
    assert state.voice_checks[0].passed
    assert 0 <= v.speech_start_s < v.speech_end_s <= v.duration_s + 0.01
