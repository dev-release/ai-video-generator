"""Pydantic models at stage boundaries. Specs: .claude/specs/*.md."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_WORDS_PER_SHOT = 45
MIN_WORDS_PER_SHOT = 3
MAX_WORDS_TOTAL = 60
# Each next shot follows the shot 2 rule (cut frame when the same speaker continues).
MAX_SHOTS = 10
MAX_CHARACTERS = 4

# Tags or stage directions in a line are either spoken aloud or shift the words.
_FORBIDDEN_IN_LINE = re.compile(r"[\[\]\(\)\*<>{}]")


class VoiceProfile(StrEnum):
    female_young_warm = "female_young_warm"
    female_adult_firm = "female_adult_firm"
    male_young_bright = "male_young_bright"
    male_adult_low = "male_adult_low"


Gender = Literal["female", "male"]


def profile_gender(profile: VoiceProfile) -> Gender:
    """Voice gender comes from the profile name (female_* / male_*); there is no neutral
    profile, so the voice always matches the face on screen."""
    return "female" if profile.value.startswith("female_") else "male"


# Words that name the gender in `look`: the video model draws from `look`.
_GENDER_WORDS: dict[str, re.Pattern[str]] = {
    "female": re.compile(r"\b(woman|women|girl|female|lady|mother|grandmother|wife|sister|"
                         r"daughter|queen|princess|goddess|heroine|she|her)\b", re.I),
    "male": re.compile(r"\b(man|men|boy|male|guy|father|grandfather|husband|brother|son|king|"
                       r"prince|god|hero|he|his)\b", re.I),
}  # fmt: skip


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Character(Frozen):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str
    # None only in scripts saved before 2026-10-01; model output (ScriptDraft) requires it.
    gender: Gender | None = Field(
        default=None, description="How the character looks on screen: female or male."
    )
    voice_profile: VoiceProfile
    language: Literal["en"] = "en"
    look: str = Field(description="On-screen look: gender, age, hair, clothes.")

    @model_validator(mode="after")
    def _voice_and_look_match_gender(self) -> Character:
        # The model describes, the code checks: voice and picture must agree.
        if self.gender is None:
            return self
        if profile_gender(self.voice_profile) != self.gender:
            raise ValueError(
                f"character {self.id}: gender is {self.gender} but voice_profile "
                f"{self.voice_profile.value} is {profile_gender(self.voice_profile)} — "
                f"choose a {self.gender}_* voice profile"
            )
        if not _GENDER_WORDS[self.gender].search(self.look):
            raise ValueError(
                f"character {self.id}: look must name the gender seen on screen ({self.gender}, "
                "e.g. 'young woman', 'old man'), also for fantasy or animal characters"
            )
        other = "male" if self.gender == "female" else "female"
        first = {g: (m.start() if (m := _GENDER_WORDS[g].search(self.look)) else 10**6)
                 for g in (self.gender, other)}  # fmt: skip
        if first[other] < first[self.gender]:
            raise ValueError(
                f"character {self.id}: look starts as {other} but gender is {self.gender}"
            )
        return self


class Delivery(StrEnum):
    """Emotion of the line (closed list); neutral = none."""

    neutral = "neutral"
    whispers = "whispers"
    softly = "softly"
    sad = "sad"
    angry = "angry"
    excited = "excited"
    worried = "worried"
    surprised = "surprised"
    sarcastically = "sarcastically"
    cautiously = "cautiously"
    happily = "happily"


def check_line(v: str) -> str:
    if v != v.strip() or not v:
        raise ValueError("a line must not be empty or start/end with spaces")
    if _FORBIDDEN_IN_LINE.search(v):
        raise ValueError(
            "a line cannot contain tags, stage directions or brackets — emotion is set separately"
        )
    n = len(v.split())
    if not MIN_WORDS_PER_SHOT <= n <= MAX_WORDS_PER_SHOT:
        raise ValueError(f"a line has {n} words; allowed {MIN_WORDS_PER_SHOT}–{MAX_WORDS_PER_SHOT}")
    return v


# Quote pairs: ASCII, typographic and guillemets. An apostrophe is not a quote (don't).
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|«([^»]+)»')


def extract_quoted(text: str) -> list[str]:
    """Quoted text verbatim, in order — each fragment is a line (a shot)."""
    return [next(g for g in m.groups() if g is not None) for m in _QUOTED.finditer(text)]


def without_quoted(text: str) -> str:
    """Text without the quoted lines — the scene description."""
    return _QUOTED.sub("", text)


def around_quoted(text: str) -> list[str]:
    """Text around the lines: [before 1st, between 1st and 2nd, …, after last]."""
    out, last = [], 0
    for m in _QUOTED.finditer(text):
        out.append(text[last : m.start()])
        last = m.end()
    return [*out, text[last:]]


class Brief(Frozen):
    """User input (CLI or UI). Lines are exactly what must be heard."""

    idea: str = Field(min_length=3, max_length=500)
    lines: list[str] = Field(min_length=1, max_length=MAX_SHOTS)

    @model_validator(mode="before")
    @classmethod
    def _lines_from_quotes(cls, data):
        # The line is taken verbatim from the quotes in the idea; the model never writes it.
        if isinstance(data, dict) and not data.get("lines"):
            found = extract_quoted(str(data.get("idea", "")))
            if not found:
                raise ValueError(
                    "no line found: put it in quotes inside the idea, e.g. "
                    'A girl finds a letter and whispers: "This handwriting is mine." '
                    "— or use --line"
                )
            data = {**data, "lines": found}
        return data

    @field_validator("lines")
    @classmethod
    def _lines_ok(cls, v: list[str]) -> list[str]:
        for line in v:
            check_line(line)
        total = sum(len(line.split()) for line in v)
        if total > MAX_WORDS_TOTAL:
            raise ValueError(f"{total} words in total > {MAX_WORDS_TOTAL} (will not fit in 30 s)")
        return v


class DraftShot(Frozen):
    """A shot from the model. No `line` field on purpose: the code inserts it from Brief."""

    character_id: str
    delivery: Delivery
    visual_prompt: str
    camera: str = "medium close-up"


class ScriptDraft(Frozen):
    title: str
    style: str = Field(description="Visual style of the whole video, for every shot.")
    characters: list[Character] = Field(min_length=1, max_length=MAX_CHARACTERS)
    shots: list[DraftShot] = Field(min_length=1, max_length=MAX_SHOTS)

    @model_validator(mode="after")
    def _gender_is_stated(self) -> ScriptDraft:
        missing = [c.id for c in self.characters if c.gender is None]
        if missing:
            raise ValueError(f"characters {missing}: gender is required (female or male)")
        looks = [" ".join(c.look.lower().split()) for c in self.characters]
        if len(set(looks)) != len(looks):
            raise ValueError("characters must have different `look` — the video draws from it")
        return self


class DraftMismatch(ValueError):
    pass


class Shot(Frozen):
    id: str = Field(pattern=r"^s[0-9]{1,2}$")
    character_id: str
    line: str = Field(description="The user's line verbatim. Never changed after the script node.")
    delivery: Delivery
    visual_prompt: str
    camera: str = "medium close-up"

    @field_validator("line")
    @classmethod
    def _line_is_clean(cls, v: str) -> str:
        return check_line(v)


class Script(Frozen):
    title: str
    style: str = Field(description="Visual style of the whole video, for every shot.")
    characters: list[Character] = Field(min_length=1, max_length=MAX_CHARACTERS)
    shots: list[Shot] = Field(min_length=1, max_length=MAX_SHOTS)

    @model_validator(mode="after")
    def _consistent(self) -> Script:
        ids = {c.id for c in self.characters}
        for s in self.shots:
            if s.character_id not in ids:
                raise ValueError(f"shot {s.id}: unknown character_id {s.character_id!r}")
        if len({s.id for s in self.shots}) != len(self.shots):
            raise ValueError("shot ids must be unique")
        total = sum(len(s.line.split()) for s in self.shots)
        if total > MAX_WORDS_TOTAL:
            raise ValueError(f"{total} words in total > {MAX_WORDS_TOTAL} (will not fit in 30 s)")
        return self

    @classmethod
    def from_draft(cls, draft: ScriptDraft, brief: Brief) -> Script:
        if len(draft.shots) != len(brief.lines):
            raise DraftMismatch(
                f"{len(draft.shots)} shots but {len(brief.lines)} lines — "
                "there must be one shot per line"
            )
        shots = [
            Shot(id=f"s{i + 1}", line=line, **d.model_dump())
            for i, (d, line) in enumerate(zip(draft.shots, brief.lines, strict=True))
        ]
        return cls(title=draft.title, style=draft.style, characters=draft.characters, shots=shots)

    def character(self, character_id: str) -> Character:
        return next(c for c in self.characters if c.id == character_id)


class CastEntry(Frozen):
    profile: VoiceProfile
    provider: str
    voice_id: str
    seed: int
    settings: dict[str, float] = {}


class VoiceClip(Frozen):
    shot_id: str
    path: str
    duration_s: float
    text: str
    # Speech window in the wav (first/last word), from voice_check.
    speech_start_s: float | None = None
    speech_end_s: float | None = None
    # Pauses between phrases (start, end) in seconds, set by the code, not by ASR.
    pauses_s: list[tuple[float, float]] = []


class Keyframe(Frozen):
    """Cut frame: shot_id starts from the frame of source_shot at at_s (spec keyframe.md)."""

    shot_id: str
    path: str
    prompt: str
    approved: bool = False
    source_shot: str | None = None
    source_sha: str | None = None  # hash of the source clip: new clip -> stale frame
    at_s: float | None = None


class Clip(Frozen):
    shot_id: str
    path: str
    duration_s: float


class ShotPlan(Frozen):
    """Exactly what goes to the video model for a shot (spec approve_script.md)."""

    shot_id: str
    character_id: str
    line: str
    delivery: str
    start: Literal["text", "cut_frame"]
    source_shot: str | None = None
    shot_s: float  # duration in the final video
    clip_s: float  # seconds requested (and billed) from the model
    prompt: str


class VideoPlan(Frozen):
    model: str
    negative_prompt: str | None = None
    cost_usd: float  # video + lipsync of all shots
    shots: list[ShotPlan]


class FinalVideo(Frozen):
    path: str
    duration_s: float
    width: int
    height: int
    lufs: float


class WordDiff(Frozen):
    op: Literal["replace", "delete", "insert"]
    expected: str
    heard: str


class AsrCheck(Frozen):
    expected: str
    heard: str
    expected_norm: str
    heard_norm: str
    wer: float
    passed: bool
    attempt: int
    model: str
    diff: list[WordDiff] = []


class VoiceCheck(Frozen):
    shot_id: str
    passed: bool
    wer: float
    heard: str
    diff: list[WordDiff] = []
    attempt: int


class ShotSync(Frozen):
    shot_id: str
    verdict: Literal["ok", "off", "n/a"]
    face_ratio: float
    best_lag_ms: float | None = None
    corr: float | None = None
    mouth_speech_ratio: float | None = None
    mouth: list[float | None] = []  # series for the UI chart
    env: list[float] = []


class SyncCheck(Frozen):
    passed: bool  # with SYNC_GATE=warn this stays True even when a shot is off
    gate: Literal["warn", "block"]
    shots: list[ShotSync]


class PipelineState(BaseModel):
    """Graph state. Each node returns a patch with its own fields only."""

    run_id: str
    run_dir: str
    brief: Brief
    script: Script | None = None
    casting: dict[str, CastEntry] = {}
    voices: list[VoiceClip] = []
    keyframes: list[Keyframe] = []
    clips: list[Clip] = []
    final: FinalVideo | None = None
    asr: AsrCheck | None = None
    verify_attempt: int = 0
    voice_attempt: int = 0
    voice_retries: dict[str, int] = {}  # shot_id -> voice retakes by the audio gate
    voice_checks: list[VoiceCheck] = []
    synced: list[Clip] = []  # clips after lipsync (video track only)
    plan_sha: str | None = None  # hash of the approved VideoPlan; another plan is not run
    sync: SyncCheck | None = None
