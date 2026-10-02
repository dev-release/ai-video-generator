"""Fake providers: no network, $0, deterministic. A test fixture, not a feature.

They mirror the real chain (spec providers.md, "Fake plan"): script from the idea, a drawn
character, animation, lips to our audio, mouth measurement. Voice: tone + echo ASR; Kokoro +
whisper with `FAKE_VOICE=kokoro`.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import numpy as np

from pipeline import registry
from pipeline.media import probe, write_atomic
from pipeline.providers import SpecProvider, Timed, puppet
from pipeline.registry import ModelSpec
from pipeline.schema import (
    Brief,
    CastEntry,
    Character,
    Delivery,
    DraftShot,
    ScriptDraft,
    VoiceProfile,
    around_quoted,
    extract_quoted,
    without_quoted,
)

# Character from the idea: (male?, elderly?) -> name, voice, on-screen look.
_CAST = {
    (False, False): ("maya", "Maya", VoiceProfile.female_young_warm,
                     "woman in her twenties, dark curly hair, oversized grey sweater"),
    (False, True): ("rosa", "Rosa", VoiceProfile.female_adult_firm,
                    "elderly woman, silver hair, red cardigan, glasses"),
    (True, False): ("leo", "Leo", VoiceProfile.male_young_bright,
                    "man in his twenties, short brown hair, navy jacket"),
    (True, True): ("victor", "Victor", VoiceProfile.male_adult_low,
                   "old man, grey hair, stubble beard, brown coat, glasses"),
}  # fmt: skip
# A second character of the same gender ("two sisters"): same profile, the code separates voices.
_CAST_2 = {
    False: ("nina", "Nina", VoiceProfile.female_young_warm,
            "young woman, straight black hair, denim jacket"),
    True: ("sam", "Sam", VoiceProfile.male_young_bright,
           "young man, curly red hair, green hoodie"),
}  # fmt: skip
_PAIR = re.compile(r"\b(two|twin)\s+\w+s\b|\b(sisters|brothers|twins|friends|couple)\b")
_MOODS = (
    ("angr|furious|shout|yell", Delivery.angry),
    ("cr[iy]|tears|sad", Delivery.sad),
    ("afraid|scared|fear|worr", Delivery.worried),
    ("happy|smil|laugh", Delivery.happily),
)


def _delivery(idea: str, line: str) -> Delivery:
    if re.search(r"whisper", idea):
        return Delivery.whispers
    if line.endswith("!"):
        return Delivery.excited
    if line.endswith("?"):
        return Delivery.surprised
    for word, d in _MOODS:
        if re.search(word, idea):
            return d
    return Delivery.softly


_REWRITE = re.compile(r"Rewrite #(\d+)")
_STAGING = ("", ", walking slowly while talking", ", glancing out of a window",
            ", talking to someone off-camera")  # fmt: skip


class FakeScriptWriter(SpecProvider):
    """A script from the idea by rules, as a model would: character, scene, emotion (no line)."""

    spec = registry.get("script", "fake")

    def write(self, brief: Brief, feedback: str | None) -> ScriptDraft:
        # Setting = the whole idea without the lines (shared, in style); a shot's action = the
        # text before its quoted line, like "…finds a letter and whispers:".
        scene = " ".join(without_quoted(brief.idea).split()).rstrip(" :,-") or "a quiet room"
        actions = [" ".join(a.split()).strip(" :,-.") for a in around_quoted(brief.idea)]
        low = scene.lower()
        old = re.search(r"\b(old|elderly|grand\w*|aged)\b", low) is not None
        cast = _who(low, old, len(brief.lines))
        speaker = _speakers(cast, actions if extract_quoted(brief.idea) else [], len(brief.lines))
        # Rewrite from approval (spec approve_script.md): different staging — framing and action.
        take = int(m.group(1)) if feedback and (m := _REWRITE.search(feedback)) else 0
        cameras = ("medium close-up", "close-up")
        staging = _STAGING[take % len(_STAGING)]
        shots = [
            DraftShot(
                character_id=speaker[i],
                delivery=_delivery(low, line),
                visual_prompt=(
                    actions[i]
                    if i < len(actions) and actions[i]
                    else scene
                    if i == 0
                    else "reaction, same place"
                )
                + staging,
                camera=cameras[(i + take) % 2],
            )  # fmt: skip
            for i, line in enumerate(brief.lines)
        ]
        return ScriptDraft(
            title=" ".join(scene.split()[:5]).title(),
            style=f"cinematic vertical drama, soft film grain. Setting: {scene}",
            characters=[
                Character(
                    id=cid,
                    name=name,
                    gender="male" if m else "female",
                    voice_profile=voice,
                    look=look,
                )
                for m, (cid, name, voice, look) in cast
            ],  # fmt: skip
            shots=shots,
        )


def _who(low: str, old: bool, n_lines: int) -> list[tuple[bool, tuple]]:
    """Who is in the scene, as a model would read it: words of both genders ("boy … girlfriend")
    -> two, in order of mention; "two sisters / friends" -> two of one gender; else one."""
    f, m = re.search(puppet.FEMALE, low), re.search(puppet.MALE, low)
    if f and m and n_lines > 1:
        first = m.start() < f.start()
        return [(first, _CAST[(first, old)]), (not first, _CAST[(not first, old)])]
    male = puppet.is_male(low)
    if _PAIR.search(low) and n_lines > 1:
        return [(male, _CAST[(male, old)]), (male, _CAST_2[male])]
    return [(male, _CAST[(male, old)])]


def _speakers(cast: list[tuple[bool, tuple]], before: list[str], n: int) -> list[str]:
    """Speaker of a line: the last gender word before it ("He smiles:" -> him) if the characters
    differ in gender; otherwise they take turns."""
    ids = [c[1][0] for c in cast]
    out = []
    for i in range(n):
        hint = before[i].lower() if i < len(before) else ""
        words = [w.group(0) for w in re.finditer(f"{puppet.MALE}|{puppet.FEMALE}", hint)]
        last = words[-1] if words else None
        who = (next((c[1][0] for c in cast if c[0] == bool(re.fullmatch(puppet.MALE, last))), None)
               if last and len({c[0] for c in cast}) > 1 else None)  # fmt: skip
        out.append(who or ids[i % len(ids)])
    return out


class FakeVideoGen(SpecProvider):
    """Like Kling: without a frame draws the character from the prompt; with a cut frame continues
    from it and moves the camera to the shot framing. Lips closed (lipsync moves them); no audio."""

    spec = registry.get("video", "fake")

    def animate(
        self, image: Path | None, motion: str, duration_s: float, out: Path, seed: int = 0
    ) -> Path:
        if image is None:
            return puppet.text_to_video(motion, duration_s, out, seed)
        return puppet.image_to_video(image, motion, duration_s, out)


_LOW_VOICES = {
    VoiceProfile.male_adult_low,
    VoiceProfile.male_young_bright,
}
SR = 44100


def babble(text: str, low: bool, shift: float = 1.0) -> np.ndarray:
    """Murmuring in the rhythm of the line: each word is a hum syllable in the character's voice,
    punctuation is a pause. No words (it is not TTS), but tempo, pauses and pitch follow speech;
    soft syllable edges without clicks."""
    f0 = (120.0 if low else 210.0) * shift
    parts: list[np.ndarray] = []
    for i, word in enumerate(text.split()):
        letters = len(re.sub(r"\W", "", word)) or 1
        dur = 0.12 + 0.045 * letters
        t = np.arange(int(dur * SR)) / SR
        pitch = f0 * (1 + 0.08 * math.sin(1.7 * i)) * (1.05 - 0.1 * t / dur)  # intonation
        phase = 2 * np.pi * np.cumsum(pitch) / SR
        voice = sum(np.sin(k * phase) / k for k in (1, 2, 3, 4))
        syllables = max(1, round(letters / 4))
        env = np.sin(np.pi * syllables * t / dur) ** 2
        parts += [0.3 * voice * env, np.zeros(int((0.28 if word[-1] in ",.;:!?" else 0.05) * SR))]
    x = np.concatenate(parts[:-1]) if parts else np.zeros(SR)
    return np.pad(x, (0, max(0, SR - len(x))))  # at least 1 s


class ToneTTS(SpecProvider):
    """Tests, CI and default fake mode, no models: murmur in the rhythm of the line (`babble`).
    The text goes to a sidecar for EchoASR."""

    spec = registry.get("tts", "tone")

    def speak(self, text: str, voice: CastEntry, seed: int, out: Path) -> Path:
        import io
        import wave

        # A second character with the same profile (voice_id "…~2") gets a higher pitch.
        variant = int(voice.voice_id.rpartition("~")[2]) if "~" in voice.voice_id else 1
        tone = babble(text, voice.profile in _LOW_VOICES, 1 + 0.18 * (variant - 1))
        pcm = (np.clip(tone, -1, 1) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        write_atomic(out, buf.getvalue())
        write_atomic(out.with_suffix(".txt"), text)
        return out


def _said(voice_dir: Path, sid: str) -> str:
    """What ToneTTS "said" in a shot: one request or phrases with pauses (voice/parts)."""
    own = voice_dir / f"{sid}.txt"
    if own.exists():
        return own.read_text()
    parts = sorted(
        (voice_dir / "parts").glob(f"{sid}.p*.txt"), key=lambda p: int(p.stem[len(sid) + 2 :])
    )
    return " ".join(p.read_text() for p in parts)


class EchoASR(SpecProvider):
    """ "Hears" exactly what ToneTTS wrote (.txt sidecars). No models — for tests and CI."""

    spec = registry.get("asr", "echo")

    def transcribe(self, wav: Path) -> str:
        own = wav.with_suffix(".txt")
        if own.exists():  # wav of a single request
            return own.read_text()
        if wav.parent.name == "voice":  # shot wav joined from phrases (audio gate)
            return _said(wav.parent, wav.stem)
        voice_dir = wav.parent / "voice"  # the final file: all shots in order
        # By shot number: string sorting would give s1, s10, s2.
        sids = sorted({p.name.split(".")[0] for p in voice_dir.rglob("s*.txt")},
                      key=lambda sid: int(sid[1:]))  # fmt: skip
        return " ".join(_said(voice_dir, sid) for sid in sids)

    def timed(self, wav: Path) -> Timed:
        # The tone sounds over the whole file: speech window = the whole wav.
        return Timed(self.transcribe(wav), 0.0, probe(wav).duration_s)


class PassthroughLipSync(SpecProvider):
    """Unit tests: the clip unchanged, no rendering (the fake plan uses FakeLipSync)."""

    spec = registry.get("lipsync", "passthrough")

    def sync(self, clip: Path, shot_audio: Path, out: Path, seed: int = 0) -> Path:
        shutil.copyfile(clip, out)
        return out


class FakeLipSync(SpecProvider):
    """The drawn character's mouth opens to the envelope of OUR shot audio."""

    spec = registry.get("lipsync", "fake")

    def sync(self, clip: Path, shot_audio: Path, out: Path, seed: int = 0) -> Path:
        return puppet.lipsync(clip, shot_audio, out)


class FakeFaceTracker(SpecProvider):
    """Mouth openness where the fake draws it (the same measure as the real tracker)."""

    spec = registry.get("face", "fake")

    def mouth(self, frames: list[bytes], width: int, height: int) -> list[float | None]:
        return puppet.mouth_openness(frames, width, height)

    def has_face(self, image: Path) -> bool | None:
        return True  # the fake frame always has the character


class NoFaceTracker(SpecProvider):
    spec = registry.get("face", "none")

    def mouth(self, frames: list[bytes], width: int, height: int) -> list[float | None]:
        return [None] * len(frames)

    def has_face(self, image: Path) -> bool | None:
        return None  # unknown: lipsync is not skipped because of a stub tracker


_FIXTURES = {
    (c.spec.capability, c.spec.name): c
    for c in (
        FakeScriptWriter, FakeVideoGen, ToneTTS, EchoASR,
        FakeLipSync, PassthroughLipSync, FakeFaceTracker, NoFaceTracker,
    )
}  # fmt: skip


def fixture(spec: ModelSpec):
    """Fake adapter: the fixture for a registry entry — no name branching in the nodes."""
    return _FIXTURES[(spec.capability, spec.name)]()
