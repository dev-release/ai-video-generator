"""VoiceProfile -> a concrete voice. The code picks the voice, never the model."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from pipeline.schema import CastEntry, Character, VoiceProfile, profile_gender

CATALOG = Path(__file__).with_name("voices.yaml")


class ConfigError(RuntimeError):
    pass


@lru_cache
def _catalog(path: Path = CATALOG) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def character_seed(character_id: str) -> int:
    # Stable across runs and machines (Python's hash() is randomized).
    return int(hashlib.sha256(character_id.encode()).hexdigest()[:8], 16) % 2**31


def _voices(profile: VoiceProfile, provider: str) -> list[dict]:
    """Voices of a profile in a catalog, main voice first (a list or a single entry)."""
    entry = (_catalog().get(profile.value) or {}).get(provider)
    entries = entry if isinstance(entry, list) else [entry] if entry else []
    return [e for e in entries if e and e.get("id")]


def resolve(
    profile: VoiceProfile,
    provider: str | None,
    character_id: str,
    taken: frozenset[str] = frozenset(),
) -> CastEntry:
    """First voice of the profile not taken by another character, then voices of other profiles
    of the same gender. provider is the catalog key; None means no catalog (placeholder tone)."""
    seed = character_seed(character_id)
    if provider is None:  # tone: a pitch variant so two characters of one profile differ
        n = next(i for i in range(1, 100) if _variant(profile.value, i) not in taken)
        return CastEntry(profile=profile, provider="none", voice_id=_variant(profile.value, n),
                         seed=seed)  # fmt: skip
    if not _voices(profile, provider):
        raise ConfigError(
            f"voices.yaml: no {provider!r} voice for profile {profile.value!r}. "
            "Add one to src/pipeline/voices.yaml."
        )
    gender = profile_gender(profile)
    order = [profile, *(p for p in VoiceProfile if p != profile and profile_gender(p) == gender)]
    for entry in (e for p in order for e in _voices(p, provider)):
        if entry["id"] not in taken:
            settings = {k: float(v) for k, v in entry.items() if k != "id"}
            return CastEntry(profile=profile, provider=provider, voice_id=entry["id"], seed=seed,
                             settings=settings)  # fmt: skip
    raise ConfigError(
        f"voices.yaml: all {gender} {provider!r} voices are taken by other characters "
        f"({len(taken)} cast) — add voices to the catalog or use fewer {gender} characters"
    )


def _variant(profile: str, n: int) -> str:
    return profile if n == 1 else f"{profile}~{n}"


def cast(
    characters: list[Character], provider: str | None, saved: dict[str, CastEntry] | None = None
) -> dict[str, CastEntry]:
    """Every character gets its own voice. A saved casting is kept (same profile and catalog) so
    a series sounds the same; a new character takes the first free voice."""
    saved = saved or {}
    out: dict[str, CastEntry] = {}
    for c in characters:
        old = saved.get(c.id)
        if (old and old.profile == c.voice_profile and old.provider == (provider or "none")
                and old.voice_id not in {e.voice_id for e in out.values()}):  # fmt: skip
            out[c.id] = old
    for c in characters:
        if c.id not in out:
            taken = frozenset(e.voice_id for e in out.values())
            out[c.id] = resolve(c.voice_profile, provider, c.id, taken)
    return {c.id: out[c.id] for c in characters}


def missing(provider: str | None) -> list[str]:
    """Profiles without a voice in the catalog (preflight)."""
    if provider is None:
        return []
    return [p.value for p in VoiceProfile if not _voices(p, provider)]
