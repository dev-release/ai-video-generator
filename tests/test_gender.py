"""Character gender <-> voice <-> picture. Spec: script.md, voice.md.

Regression: Claude described a "young mystical humanoid forest guardian" with no gender and a
neutral voice profile -> a male Kokoro voice while Kling drew a woman.
"""

from __future__ import annotations

import json

import pytest
import yaml
from pydantic import ValidationError

from pipeline.nodes import script
from pipeline.providers import fake
from pipeline.schema import Brief, Character, ScriptDraft, VoiceProfile, profile_gender
from pipeline.voices import CATALOG
from tests.conftest import LINE_1

WOMAN = "young woman with long white hair, leaf tunic"


def test_every_profile_has_a_gender_and_no_neutral_voice_for_on_screen_characters():
    assert {profile_gender(p) for p in VoiceProfile} == {"female", "male"}
    assert "narrator_neutral" not in {p.value for p in VoiceProfile}


def test_catalog_voice_gender_matches_profile():
    # Kokoro VOICES.md: the second letter of an id is the gender (af_/bf_ female, am_/bm_ male).
    catalog = yaml.safe_load(CATALOG.read_text())
    for profile in VoiceProfile:
        for v in catalog[profile.value]["kokoro"]:  # every voice of the profile, not only the main
            assert {"f": "female", "m": "male"}[v["id"][1]] == profile_gender(profile), (profile, v)


def test_voice_profile_must_match_character_gender():
    with pytest.raises(ValidationError, match="voice_profile"):
        Character(id="lumi", name="Lumi", gender="female",
                  voice_profile=VoiceProfile.male_adult_low, look=WOMAN)  # fmt: skip


@pytest.mark.parametrize(
    ("gender", "look"),
    [
        ("female", "young mystical humanoid forest guardian with silver-blue skin"),
        ("male", "young woman with long white hair"),  # the picture would say the opposite
    ],
)
def test_look_must_name_the_gender_the_video_will_show(gender, look):
    profile = VoiceProfile.female_young_warm if gender == "female" else VoiceProfile.male_adult_low
    with pytest.raises(ValidationError, match="look"):
        Character(id="lumi", name="Lumi", gender=gender, voice_profile=profile, look=look)


def test_owner_case_goes_back_to_the_model_and_ends_with_a_matching_voice(make_ctx, make_state):
    """A response like that run -> fix loop with an explanation -> the voice matches the gender."""
    bad = json.dumps({
        "title": "Lumi", "style": "fantasy",
        "characters": [{"id": "lumi", "name": "Lumi", "gender": "female",
                        "voice_profile": "male_young_bright", "language": "en",
                        "look": "Young mystical humanoid forest guardian with silver-blue skin"}],
        "shots": [{"character_id": "lumi", "delivery": "softly", "visual_prompt": "jungle",
                   "camera": "medium close-up"}],
    })  # fmt: skip

    class FirstWrong(fake.FakeScriptWriter):
        calls: list[str | None] = []

        def write(self, brief, feedback):
            FirstWrong.calls.append(feedback)
            if len(FirstWrong.calls) == 1:
                return ScriptDraft.model_validate_json(bad)  # like the Claude adapter
            return super().write(brief, feedback)

    state = make_state(lines=(LINE_1,), idea="A young woman guards a magic forest")
    patch = script.run(state, make_ctx(script=FirstWrong()))
    assert FirstWrong.calls[0] is None and "gender" in (FirstWrong.calls[1] or "")
    c = patch["script"].characters[0]
    assert profile_gender(c.voice_profile) == c.gender == "female"


@pytest.mark.parametrize(
    "idea", ["A girl finds a letter", "An old man finds a letter", "A boy wins", "A grandma cries"]
)
def test_fake_script_cast_is_consistent(idea):
    draft = fake.FakeScriptWriter().write(Brief(idea=idea, lines=[LINE_1]), None)
    c = draft.characters[0]
    assert profile_gender(c.voice_profile) == c.gender


def test_model_must_state_gender_but_old_saved_scripts_still_load():
    from pipeline.schema import Script

    legacy = {"id": "mia", "name": "Mia", "voice_profile": "female_young_warm",
              "look": "19-year-old girl"}  # fmt: skip
    shot = {"id": "s1", "character_id": "mia", "line": LINE_1, "delivery": "neutral",
            "visual_prompt": "room"}  # fmt: skip
    Script.model_validate({"title": "t", "style": "s", "characters": [legacy], "shots": [shot]})
    draft = {"title": "t", "style": "s", "characters": [legacy],
             "shots": [{k: v for k, v in shot.items() if k not in ("id", "line")}]}  # fmt: skip
    with pytest.raises(ValidationError, match="gender is required"):
        ScriptDraft.model_validate(draft)


def test_model_schema_cannot_skip_gender():
    from pipeline.providers.claude import output_schema

    g = output_schema()["$defs"]["Character"]["properties"]["gender"]
    assert g["enum"] == ["female", "male"] and "anyOf" not in g
    assert "gender" in output_schema()["$defs"]["Character"]["required"]
