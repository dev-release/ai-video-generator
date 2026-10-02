"""Fake plan: a character speaking our line. Spec: .claude/specs/providers.md."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pipeline.config import Settings
from pipeline.graph import Runner
from pipeline.media import probe, read_frames, run_ffmpeg, video_frames
from pipeline.nodes.video import scene_prompt
from pipeline.providers import build, fake, puppet
from pipeline.schema import Brief, Delivery, VoiceProfile

MAYA = "woman in her twenties, dark curly hair, oversized grey sweater"
VICTOR = "old man, grey hair, stubble beard, brown coat, glasses"


def frame(tmp_path: Path, look: str, visual: str, name: str, seed: int = 0) -> np.ndarray:
    """First frame of a fake shot "from text" (like shot 1 in Kling text-to-video)."""
    out = tmp_path / f"{name}.mp4"
    prompt = scene_prompt("cinematic", look, visual, "close-up")
    fake.FakeVideoGen().animate(None, prompt, 1.0, out, seed)
    return next(read_frames(out, puppet.FPS)).astype(int)


def png(tmp_path: Path, look: str = MAYA, name: str = "k") -> Path:
    """Cut frame for a shot "from a frame"."""
    import cv2

    out = tmp_path / f"{name}.png"
    img = puppet.draw_portrait(puppet.parse_look(look, "kitchen"))
    cv2.imwrite(str(out), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return out


def face(img: np.ndarray) -> np.ndarray:
    return img[700:1100, 380:700]  # eyes, nose, mouth, no background


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())  # 0..255; ~1–2 is compression noise


def test_character_follows_look_and_stays_the_same_across_shots(tmp_path):
    a = frame(tmp_path, MAYA, "she reads a letter in the kitchen", "a")
    b = frame(tmp_path, MAYA, "close-up of her eyes at night", "b")
    other = frame(tmp_path, VICTOR, "she reads a letter in the kitchen", "c")
    assert a.shape == (1920, 1080, 3)
    assert diff(face(a), face(b)) < 3  # same look -> same character
    assert diff(a[:200], b[:200]) > 10  # another scene -> another background
    assert diff(face(a), face(other)) > 10


def test_new_take_is_a_different_clip(tmp_path):
    # A take shifts the seed by TAKE_STRIDE: the clip must change, otherwise its cut frame would
    # match the old one and the hash-bound approval would think the frame is the same.
    assert diff(frame(tmp_path, MAYA, "x", "a"), frame(tmp_path, MAYA, "x", "b", 1000)) > 1


def test_parse_look_reads_hair_clothes_and_mood():
    look = puppet.parse_look(VICTOR, "a rainy night street")
    assert not look.female and look.old and look.beard and look.glasses
    assert look.hair == puppet.HAIR["grey"] and look.cloth == puppet.CLOTH["brown"]
    assert look.bg == dict(puppet.MOODS)[r"night|dark|moody|noir|midnight|shadow"]


@pytest.mark.parametrize(
    ("idea", "voice", "delivery"),
    [
        ("An old man finds a letter from his late wife and whispers", VoiceProfile.male_adult_low,
         Delivery.whispers),
        ("A girl finds a letter from her future self", VoiceProfile.female_young_warm,
         Delivery.softly),
        ("A boy wins the lottery", VoiceProfile.male_young_bright, Delivery.excited),
    ],
)  # fmt: skip
def test_fake_script_follows_idea_and_line(idea, voice, delivery):
    line = "We did it, we really did it!" if "lottery" in idea else "I never wrote this letter."
    brief = Brief(idea=f'{idea}: "{line}"', lines=[])
    draft = fake.FakeScriptWriter().write(brief, None)
    assert draft.characters[0].voice_profile == voice
    assert draft.shots[0].delivery == delivery
    assert line not in draft.shots[0].visual_prompt  # the line is not drawn into the frame


@pytest.mark.parametrize("from_frame", [False, True])
def test_fake_video_is_silent_and_of_requested_length(tmp_path, from_frame):
    start = png(tmp_path) if from_frame else None
    prompt = "reaction. Camera: close-up." if from_frame else scene_prompt("c", MAYA, "k", "m")
    out = fake.FakeVideoGen().animate(start, prompt, 3.0, tmp_path / "c.mp4")
    p = probe(out)
    assert (
        abs(p.duration_s - 3.0) < 0.05 and not p.has_audio and (p.width, p.height) == (1080, 1920)
    )


def test_shot_from_cut_frame_starts_on_it_and_moves_camera_to_its_framing(tmp_path):
    import cv2

    start = png(tmp_path)
    out = fake.FakeVideoGen().animate(start, "turns the page. Camera: close-up.", 3.0,
                                      tmp_path / "s2.mp4")  # fmt: skip
    frames = list(read_frames(out, puppet.FPS))
    base = cv2.cvtColor(cv2.imread(str(start)), cv2.COLOR_BGR2RGB).astype(int)
    assert diff(frames[0].astype(int), base) < 3  # no jump: the start is the cut frame
    assert abs(puppet.mouth_scale(frames[-1]) - puppet.framing_zoom("close-up")) < 0.08


def dark(img: np.ndarray) -> float:
    """Share of mouth cavity pixels in the mouth area: an open mouth is a dark cavity."""
    h, w = img.shape[:2]
    cx, cy = round(puppet.MOUTH[0] * w), round(puppet.MOUTH[1] * h)
    r = round(puppet.MOUTH_HW * w)
    roi = img[cy - r : cy + r, cx - r : cx + r].astype(int)
    return float((np.abs(roi - puppet.MOUTH_INSIDE).sum(axis=2) < 40).mean())


def test_lipsync_opens_mouth_only_while_our_audio_speaks(tmp_path):
    clip = fake.FakeVideoGen().animate(png(tmp_path), "m", 3.0, tmp_path / "c.mp4")
    audio = tmp_path / "lip.wav"  # 1 s silence, 1 s "speech", 1 s silence
    speech = "aevalsrc='if(between(t,1,2),0.8*sin(2*PI*220*t),0)':s=44100:d=3"
    run_ffmpeg(["-f", "lavfi", "-i", speech, "-ac", "1", str(audio)])
    out = fake.FakeLipSync().sync(clip, audio, tmp_path / "l.mp4")
    assert not probe(out).has_audio  # the final audio is only ours (assemble)
    d = [dark(f) for f in read_frames(out, puppet.FPS)]
    assert np.mean(d[35:55]) > 0.1 and max(d[:25] + d[70:]) < 0.005  # 0.005 is compression noise
    frames, w, h = video_frames(out, 0, 3.0, 30)
    opening = fake.FakeFaceTracker().mouth(frames, w, h)
    assert np.mean([a or 0 for a in opening[35:55]]) > 0.3  # mouth open in speech
    assert (
        max(a or 0 for a in opening[5:25] + opening[70:]) < 0.1
    )  # and closed in silence (compression noise)


def test_fake_mode_uses_the_whole_speaking_chain():
    p = build(Settings(_env_file=None, pipeline_providers="fake"))
    assert isinstance(p.lipsync, fake.FakeLipSync) and isinstance(p.face, fake.FakeFaceTracker)


def test_fake_run_has_a_face_and_sync_is_measured(tmp_path):
    s = Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )
    lines = ["This handwriting is mine, but I never wrote this letter.", "Who sent it to me?"]
    r = Runner(s).start(Brief(idea="A girl finds a letter", lines=lines))
    assert r.status == "done", r.error
    assert r.state and r.state.sync
    for shot in r.state.sync.shots:
        assert shot.face_ratio > 0.9
        assert shot.verdict == "ok", shot  # the mouth is synced by construction: the reference


@pytest.mark.parametrize("zoom", [1.0, 1.3, 1.6])
def test_framing_is_read_back_from_the_frame(zoom):
    # Fake stages read the framing from the frame itself (the lip line), like real models.
    img = puppet.draw_portrait(puppet.parse_look(MAYA, "kitchen"), zoom=zoom)
    assert abs(puppet.mouth_scale(img) - zoom) < 0.06
    assert puppet.framing_zoom("medium close-up") == 1.0
    assert puppet.framing_zoom("close-up") == 1.3 and puppet.framing_zoom("extreme close-up") == 1.6


def test_fake_script_takes_each_shot_action_from_text_before_its_line():
    idea = (
        'She finds a letter and whispers: "Wait... it is mine." '
        'Then she turns the page: "Three days left."'
    )
    draft = fake.FakeScriptWriter().write(Brief(idea=idea, lines=[]), None)
    assert [s.visual_prompt for s in draft.shots] == [
        "She finds a letter and whispers", "Then she turns the page"
    ]  # fmt: skip
    assert [s.camera for s in draft.shots] == ["medium close-up", "close-up"]
    assert "Then she turns the page" in draft.style  # the setting is shared by shots


def test_shot2_continues_from_the_cut_frame_then_moves_to_its_framing(tmp_path):
    """The context must not be lost, the transition must be clean: shot 2 starts from the cut
    frame, so there is no jump at the cut, then the camera moves to shot 2 framing."""
    s = Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )
    idea = (
        'She finds a letter and whispers: "Wait... it is mine." '
        'Then she turns the page: "Three days left to stop it."'
    )
    r = Runner(s).start(Brief(idea=idea, lines=[]))
    assert r.status == "done", r.error
    assert r.state and r.state.asr and r.state.asr.passed  # both lines verbatim, in order
    assert r.state.asr.expected == "Wait... it is mine. Three days left to stop it."
    from pipeline.timeline import shot_durations

    d1 = shot_durations(r.state, s.pacing())["s1"]
    frames = [f.astype(int) for f in read_frames(Path(r.state.final.path), puppet.FPS)]
    cut = round(d1 * puppet.FPS)
    jump = diff(frames[cut - 1], frames[cut])
    assert jump < 3, jump  # no jump: shot 2 starts on the last frame of shot 1
    assert abs(puppet.mouth_scale(frames[-1]) - puppet.framing_zoom("close-up")) < 0.1
    assert r.state.sync and all(x.verdict == "ok" for x in r.state.sync.shots)
