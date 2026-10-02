"""Spec: .claude/specs/script.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.nodes import script
from pipeline.providers import fake
from pipeline.schema import Brief, DraftShot, ScriptDraft
from tests.conftest import LINE_1, LINE_2


class CountingWriter(fake.FakeScriptWriter):
    def __init__(self, drafts: list[ScriptDraft] | None = None) -> None:
        self.calls: list[str | None] = []
        self._drafts = drafts

    def write(self, brief: Brief, feedback: str | None) -> ScriptDraft:
        self.calls.append(feedback)
        if self._drafts:
            return self._drafts[min(len(self.calls), len(self._drafts)) - 1]
        return super().write(brief, feedback)


@pytest.mark.parametrize(
    "line",
    ["[whispers] I never wrote this.", "I never (really) wrote this.", "I never *wrote* this."],
)
def test_line_with_tags_rejected_before_model(line):
    with pytest.raises(ValidationError):
        Brief(idea="a letter", lines=[line])


def test_more_than_max_shots_lines_rejected():
    # Up to 10 shots; MAX_SHOTS is the limit, beyond it an error before any model call.
    from pipeline.schema import MAX_SHOTS

    Brief(idea="a letter", lines=["One two three."] * MAX_SHOTS)
    with pytest.raises(ValidationError):
        Brief(idea="a letter", lines=["One two three."] * (MAX_SHOTS + 1))


def test_too_many_words_total_rejected():
    long = " ".join(["word"] * 40)
    with pytest.raises(ValidationError):
        Brief(idea="a letter", lines=[long, long])


def test_model_schema_has_no_line_field():
    # The model cannot paraphrase the line: its schema has no such field.
    assert '"line"' not in json.dumps(ScriptDraft.model_json_schema())


def test_lines_land_in_script_byte_for_byte(make_ctx, make_state, tmp_path):
    state = make_state()
    patch = script.run(state, make_ctx())
    assert [s.line for s in patch["script"].shots] == [LINE_1, LINE_2]
    on_disk = json.loads(Path(tmp_path, "script.json").read_text())
    assert [s["line"] for s in on_disk["shots"]] == [LINE_1, LINE_2]
    assert json.loads(Path(tmp_path, "brief.json").read_text())["lines"] == [LINE_1, LINE_2]


def test_shot_count_mismatch_goes_to_fix_loop(make_ctx, make_state):
    good = fake.FakeScriptWriter().write(Brief(idea="x x x", lines=[LINE_1, LINE_2]), None)
    short = good.model_copy(update={"shots": good.shots[:1]})
    writer = CountingWriter([short, good])
    patch = script.run(make_state(), make_ctx(script=writer))
    assert len(writer.calls) == 2
    assert writer.calls[0] is None and "2 lines" in writer.calls[1]
    assert len(patch["script"].shots) == 2


def test_unknown_character_goes_to_fix_loop_then_stops(make_ctx, make_state):
    good = fake.FakeScriptWriter().write(Brief(idea="x x x", lines=[LINE_1]), None)
    bad_shot = DraftShot(character_id="ghost", delivery="sad", visual_prompt="empty room")
    bad = good.model_copy(update={"shots": [bad_shot]})
    writer = CountingWriter([bad])
    with pytest.raises(script.ScriptInvalid, match="ghost"):
        script.run(make_state(lines=(LINE_1,)), make_ctx(script=writer))
    assert len(writer.calls) == script.MAX_SCRIPT_FIX_ATTEMPTS + 1


def test_idempotent_second_run_does_not_call_model(make_ctx, make_state):
    writer = CountingWriter()
    ctx = make_ctx(script=writer)
    first = script.run(make_state(), ctx)
    second = script.run(make_state(), ctx)
    assert len(writer.calls) == 1
    assert first["script"] == second["script"]


def test_changed_line_invalidates_cache(make_ctx, make_state):
    writer = CountingWriter()
    ctx = make_ctx(script=writer)
    script.run(make_state(), ctx)
    patch = script.run(make_state(lines=(LINE_1, "It says I have three days left.")), ctx)
    assert len(writer.calls) == 2
    assert patch["script"].shots[1].line == "It says I have three days left."


@pytest.mark.parametrize(
    ("idea", "lines"),
    [
        ('She reads it and whispers: "This handwriting is mine."', ["This handwriting is mine."]),
        ("He says “I never wrote this letter.”", ["I never wrote this letter."]),
        ("She says «I never wrote this letter.»", ["I never wrote this letter."]),
        (
            'Two shots. "I found it in May." Then: "It says I have 3 days."',
            ["I found it in May.", "It says I have 3 days."],
        ),
        ('A kid asks "Mom can\'t see you, can she?"', ["Mom can't see you, can she?"]),
    ],
)
def test_lines_extracted_verbatim_from_quotes(idea, lines):
    assert Brief(idea=idea, lines=[]).lines == lines
    assert Brief.model_validate({"idea": idea}).lines == lines


def test_no_quotes_and_no_lines_is_explained_error():
    with pytest.raises(ValidationError, match="quotes"):
        Brief.model_validate({"idea": "A girl finds a letter from her future self"})


def test_explicit_lines_win_over_quotes():
    b = Brief(idea='She says "Something else entirely here."', lines=[LINE_1])
    assert b.lines == [LINE_1]


def test_more_quoted_fragments_than_max_shots_rejected():
    from pipeline.schema import MAX_SHOTS

    quoted = " ".join(['"One two three."'] * (MAX_SHOTS + 1))
    with pytest.raises(ValidationError):
        Brief.model_validate({"idea": quoted})
    three = Brief.model_validate({"idea": '"One two three." "Four five six." "Seven eight nine."'})
    assert len(three.lines) == 3


def test_script_prompt_asks_for_speaking_shots_lipsync_can_use():
    # spec script.md: the face is visible, the mouth not covered, one speaker; shot 2 of the same
    # character continues the same moment (from the cut frame).
    from pipeline.providers import claude

    text = claude.SYSTEM.lower()
    for rule in ("mouth", "only the speaker", "same moment", "off-camera", "walk"):
        assert rule in text, rule
    # The character need not look at the camera.
    assert "do not have them look" not in text and "facing the camera or" not in text
