"""Several characters -> several voices. Spec: voice.md (casting), script.md (characters).

A boy and a girl must sound different; this belongs to the script and then the voice. Before,
voices differed only because Claude picked different profiles; two girls would have shared one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pipeline import voices
from pipeline.providers import fake
from pipeline.schema import (
    MAX_CHARACTERS,
    Brief,
    CastEntry,
    Character,
    DraftShot,
    ScriptDraft,
    VoiceProfile,
    profile_gender,
)
from tests.test_graph import runner

F = VoiceProfile.female_young_warm


def woman(cid: str, hair: str, profile: VoiceProfile = F) -> Character:
    return Character(id=cid, name=cid.title(), gender="female", voice_profile=profile,
                     look=f"young woman, {hair} hair")  # fmt: skip


def test_catalog_gives_every_gender_enough_distinct_voices():
    catalog = yaml.safe_load(voices.CATALOG.read_text())
    ids = [v["id"] for p in VoiceProfile for v in catalog[p.value]["kokoro"]]
    assert len(ids) == len(set(ids))  # a voice belongs to one profile
    for gender in ("female", "male"):
        n = sum(
            len(catalog[p.value]["kokoro"]) for p in VoiceProfile if profile_gender(p) == gender
        )
        assert n >= MAX_CHARACTERS, gender


def test_two_characters_with_the_same_profile_get_different_voices():
    cast = voices.cast([woman("anna", "red"), woman("kate", "black")], "kokoro")
    assert [cast[c].voice_id for c in ("anna", "kate")] == ["af_heart", "af_bella"]
    assert cast["kate"].profile == F  # the profile is the model's choice; the code split the voices


def test_four_women_get_four_different_female_voices_and_a_fifth_is_a_clear_error():
    four = [woman(f"w{i}", h) for i, h in enumerate(("red", "black", "blond", "grey"))]
    cast = voices.cast(four, "kokoro")
    ids = [cast[c.id].voice_id for c in four]
    assert len(set(ids)) == 4 and all(i[1] == "f" for i in ids)  # af_/bf_ are female (VOICES.md)
    with pytest.raises(voices.ConfigError, match="taken"):
        voices.cast([*four, woman("w5", "white")], "kokoro")


def test_tone_voice_also_differs_for_two_characters_of_one_profile():
    cast = voices.cast([woman("anna", "red"), woman("kate", "black")], None)
    assert cast["anna"].voice_id != cast["kate"].voice_id


def test_saved_casting_is_kept_and_a_new_character_gets_a_free_voice():
    # Rewrite the script: Anna keeps her voice, the new Kate takes a free one.
    saved = {"anna": CastEntry(profile=F, provider="kokoro", voice_id="af_bella", seed=1)}
    cast = voices.cast([woman("anna", "red"), woman("kate", "black")], "kokoro", saved)
    assert cast["anna"].voice_id == "af_bella" and cast["kate"].voice_id == "af_heart"


def test_script_allows_up_to_four_characters_with_distinct_looks():
    shot = DraftShot(character_id="w0", delivery="neutral", visual_prompt="room")

    def draft(chars):
        return ScriptDraft(title="t", style="s", characters=chars, shots=[shot])

    hair = ("red", "black", "blond", "grey", "white")
    draft([woman(f"w{i}", hair[i]) for i in range(MAX_CHARACTERS)])
    with pytest.raises(ValidationError):
        draft([woman(f"w{i}", hair[i]) for i in range(MAX_CHARACTERS + 1)])
    with pytest.raises(ValidationError, match="look"):  # the video would draw one person
        draft([woman("w0", "red"), woman("w1", "red")])


@pytest.mark.parametrize(
    ("idea", "genders", "speakers"),
    [
        # A boy and a girl, a dialogue without hints: the speakers alternate.
        ("scene in Paris, boy walking with his girlfriend in a square and they talking",
         ("male", "female"), ("male", "female")),
        # The speaker comes from the text before the line.
        ('She opens the door. "Who are you?" He smiles: "I am your brother." '
         'She: "No way, not you."',
         ("female", "male"), ("female", "male", "female")),
        # Two people of one gender: two characters, the code separates the voices.
        ("Two sisters argue in the kitchen", ("female", "female"), ("female", "female")),
        # A monologue: one character.
        ("A girl finds a letter from herself", ("female",), ("female", "female")),
    ],
)  # fmt: skip
def test_fake_script_understands_who_is_in_the_scene(idea, genders, speakers):
    from pipeline.schema import extract_quoted

    lines = extract_quoted(idea) or ["This is a beautiful place.", "Yes, it is calm here."]
    d = fake.FakeScriptWriter().write(Brief(idea=idea, lines=lines), None)
    by_id = {c.id: c for c in d.characters}
    assert tuple(c.gender for c in d.characters) == genders
    assert tuple(by_id[s.character_id].gender for s in d.shots) == speakers[: len(lines)]


@pytest.mark.parametrize(
    "idea",
    [
        'A boy walks with his girlfriend. He says: "This place is calm." She says: "Yes, I agree."',
        'Two sisters argue in the kitchen: "You took my dress." Then: "You never wear it."',
    ],
)
def test_each_character_speaks_with_its_own_voice_end_to_end(tmp_path, idea):
    r = runner(tmp_path).start(Brief.model_validate({"idea": idea}))
    assert r.status == "done", r.error
    run = Path(r.state.run_dir)
    cast = json.loads((run / "casting.json").read_text())
    assert len(cast) == 2 and len({c["voice_id"] for c in cast.values()}) == 2
    chars = {c.id: c for c in r.state.script.characters}
    for shot in r.state.script.shots:
        req = json.loads((run / "voice" / f"{shot.id}.request.json").read_text())
        assert req["voice_id"] == cast[shot.character_id]["voice_id"]  # the speaker's own voice
        assert (
            profile_gender(chars[shot.character_id].voice_profile)
            == chars[shot.character_id].gender
        )
    assert r.state.script.shots[0].character_id != r.state.script.shots[1].character_id
