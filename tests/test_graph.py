"""Spec: pipeline.md — graph, HITL via interrupt, verify loop, idempotency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import runmemo
from pipeline.config import Settings
from pipeline.graph import Runner
from pipeline.providers import Providers, fake
from pipeline.schema import Brief
from tests.conftest import LINE_1, LINE_2

BRIEF = Brief(idea="A letter from the future", lines=[LINE_1, LINE_2])


def paid(p):
    """Act like a paid model: the shared cache across runs is only for paid ones."""
    p.spec = p.spec.model_copy(update={"price": p.spec.price.model_copy(update={"usd": 0.001})})
    return p


class CountingTTS(fake.ToneTTS):
    def __init__(self) -> None:
        self.calls = 0

    def speak(self, text, voice, seed, out):
        self.calls += 1
        return super().speak(text, voice, seed, out)


class DeafOnFinalASR(fake.EchoASR):
    """Hears the shot voice right (the audio gate passes) but not the final file."""

    name = "deaf-final"

    def transcribe(self, wav: Path) -> str:
        return super().transcribe(wav) if wav.with_suffix(".txt").exists() else "something else"


class DeafASR(fake.EchoASR):
    """Does not hear the line even in the shot wav: the audio gate must fire before video."""

    name = "deaf"

    def transcribe(self, wav: Path) -> str:
        return "something else entirely"


def runner(tmp_path, auto_approve=True, **overrides) -> Runner:
    s = Settings(
        _env_file=None,
        pipeline_providers="fake",
        fake_voice="tone",
        runs_dir=tmp_path / "runs",
        auto_approve=auto_approve,
    )
    base = dict(
        script=fake.FakeScriptWriter(),
        video=fake.FakeVideoGen(),
        tts=fake.ToneTTS(),
        asr=fake.EchoASR(),
    )
    return Runner(s, Providers(**{**base, **overrides}))


def past_script(rn: Runner, r):
    """First stop with --review: the script with video prompts (spec approve_script.md)."""
    assert r.status == "waiting_approval" and r.interrupt["kind"] == "approve_script", r.error
    return rn.resume(r.run_id, {"script": "approve"})


def test_happy_path_auto_approve(tmp_path):
    r = runner(tmp_path).start(BRIEF)
    assert r.status == "done", r.error
    assert r.state.asr.passed and Path(r.state.final.path).exists()
    assert r.run_id.count("-") == 3  # YYYY-MM-DD-NN


def test_pauses_between_shots_with_the_cut_frame_then_resumes(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, video=vid)
    r = past_script(rn, rn.start(BRIEF))
    assert r.status == "waiting_approval"
    # Pause after shot 1, before paying for shot 2: cut frame of s2 from clip s1 (keyframe.md).
    [f] = r.interrupt["frames"]
    assert (f["shot_id"], f["source_shot"]) == ("s2", "s1") and f["at_s"] > 0
    assert vid.calls == 1 and [c.shot_id for c in r.state.clips] == ["s1"]
    done = rn.resume(r.run_id, {"s2": "approve"})
    assert done.status == "done", done.error
    assert vid.starts == [None, "s2.png"]  # shot 1 from text, shot 2 from the cut frame


def test_one_shot_has_no_cut_frame_pause(tmp_path):
    rn = runner(tmp_path, auto_approve=False)
    r = past_script(rn, rn.start(Brief(idea="a letter", lines=[LINE_1])))
    assert r.status == "done", r.error  # no cut, so nothing to stop at after the script


def test_other_take_regenerates_the_previous_shot_and_asks_again(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, video=vid)
    r = past_script(rn, rn.start(BRIEF))
    first_frame = Path(r.interrupt["frames"][0]["path"]).read_bytes()
    r = rn.resume(r.run_id, {"s2": "regenerate"})  # "another take" = a new take of video s1
    assert r.status == "waiting_approval"
    assert vid.calls == 2 and vid.starts == [None, None]  # s1 again, s2 not yet
    assert Path(r.interrupt["frames"][0]["path"]).read_bytes() != first_frame
    assert rn.resume(r.run_id, {"s2": "approve"}).status == "done"
    assert vid.calls == 3


def test_invalid_decision_is_explained_and_does_not_stick(tmp_path):
    # A bad answer returns the same pause with an explanation, not an error: LangGraph replays a
    # node's answer, so raising after it would repeat on every resume (the run would be stuck).
    rn = runner(tmp_path, auto_approve=False)
    r = rn.start(BRIEF)
    bad = rn.resume(r.run_id, {"s2": "approve"})  # a frame decision while the pause is the script
    assert bad.status == "waiting_approval" and bad.interrupt["kind"] == "approve_script"
    assert "invalid decision" in bad.interrupt["error"]
    r = rn.resume(r.run_id, {"script": "approve"})  # a valid decision after a bad one passes
    assert r.status == "waiting_approval" and r.interrupt["kind"] == "approve_cut_frames", r.error
    bad = rn.resume(r.run_id, {"s2": "maybe"})
    assert bad.status == "waiting_approval" and "invalid decision" in bad.interrupt["error"]
    assert rn.resume(r.run_id, {"s2": "approve"}).status == "done"


def test_final_gate_failure_stops_without_paid_regeneration(tmp_path):
    tts, vid = CountingTTS(), CountingVideo()
    r = runner(tmp_path, tts=tts, video=vid, asr=DeafOnFinalASR()).start(BRIEF)
    assert r.status == "failed_verify", r.error
    # The voice passed the audio gate: a final fail does not drag in new voice, video and lipsync.
    assert tts.calls == 2 and vid.calls == 2
    assert Path(r.state.final.path).exists()  # the file stays, marked passed=false


def test_second_run_same_id_calls_no_providers(tmp_path):
    tts, vid = CountingTTS(), CountingVideo()
    rn = runner(tmp_path, tts=tts, video=vid)
    first = rn.start(BRIEF)
    again = rn.start(BRIEF, run_id=first.run_id)
    assert again.status == "done"
    assert tts.calls == 2 and vid.calls == 2


def test_partial_regeneration_of_voice_keeps_frames_and_clips(tmp_path):
    tts, vid = CountingTTS(), CountingVideo()
    rn = runner(tmp_path, tts=tts, video=vid)
    first = rn.start(BRIEF)
    run_dir = Path(first.state.run_dir)
    clip_before = (run_dir / "clips" / "s1.mp4").stat().st_mtime_ns
    runmemo.bump_take(run_dir, "voice", "s2")  # a new voice take for s2 (UI / --regen)
    again = rn.start(BRIEF, run_id=first.run_id)
    assert again.status == "done", again.error
    assert tts.calls == 3 and vid.calls == 2  # s2 voice has the same length: video from cache
    assert (run_dir / "clips" / "s1.mp4").stat().st_mtime_ns == clip_before
    seeds = [json.loads((run_dir / "voice" / f"{s}.request.json").read_text())["seed"]
             for s in ("s1", "s2")]  # fmt: skip
    assert seeds[1] - seeds[0] >= 1000  # a take never repeats the seed of earlier attempts


def test_new_take_of_shot1_makes_a_new_cut_frame_that_needs_human(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, video=vid)
    r = past_script(rn, rn.start(BRIEF))
    r = rn.resume(r.run_id, {"s2": "approve"})
    assert r.status == "done"
    runmemo.bump_take(r.state.run_dir, "video", "s1")  # UI / --regen video:s1
    again = rn.start(BRIEF, run_id=r.run_id)
    # Same video plan: the script is not asked again; a new cut frame needs a human.
    assert again.status == "waiting_approval" and again.interrupt["kind"] == "approve_cut_frames"
    assert [f["shot_id"] for f in again.interrupt["frames"]] == ["s2"]
    assert vid.calls == 3  # s1, s2, new s1; s2 after approval


def test_same_input_in_another_run_costs_nothing(tmp_path):
    tts, vid = paid(CountingTTS()), paid(CountingVideo())
    rn = runner(tmp_path, tts=tts, video=vid)
    rn.start(BRIEF)
    second = rn.start(BRIEF)  # another run_id, same input
    assert second.status == "done"
    assert tts.calls == 2 and vid.calls == 2


def test_free_models_are_regenerated_not_taken_from_old_runs(tmp_path):
    # Free results from the cache of past runs would hide changes in our code — including the
    # script node, which caches on its own.
    from pipeline.providers import fake as f

    class CountingScript(f.FakeScriptWriter):
        calls = 0

        def write(self, brief, feedback):
            CountingScript.calls += 1
            return super().write(brief, feedback)

    tts = CountingTTS()
    rn = runner(tmp_path, tts=tts, script=CountingScript())
    rn.start(BRIEF)
    rn.start(BRIEF)
    assert tts.calls == 4 and CountingScript.calls == 2


def test_fresh_run_ignores_shared_cache(tmp_path):
    tts = paid(CountingTTS())
    rn = runner(tmp_path, tts=tts)
    rn.start(BRIEF)
    from pipeline.graph import new_run_id

    rid = new_run_id(tmp_path / "runs")
    runmemo.set_fresh(tmp_path / "runs" / rid)
    rn.start(BRIEF, run_id=rid)
    assert tts.calls == 4


class FaceKnown(fake.NoFaceTracker):
    def __init__(self, has: bool):
        self.has = has

    def has_face(self, image):
        return self.has


class CountingLipSync(fake.PassthroughLipSync):
    def __init__(self):
        self.calls = 0

    def sync(self, clip, shot_audio, out, seed=0):
        self.calls += 1
        from pipeline.media import probe

        assert abs(probe(shot_audio).duration_s - probe(clip).duration_s) < 0.05  # audio = clip
        return super().sync(clip, shot_audio, out, seed)


def test_no_face_on_frame_skips_lipsync(tmp_path):
    ls = CountingLipSync()
    r = runner(tmp_path, lipsync=ls, face=FaceKnown(False)).start(BRIEF)
    assert r.status == "done" and ls.calls == 0


def test_face_on_frame_runs_lipsync_with_audio_trimmed_to_clip(tmp_path):
    ls = CountingLipSync()
    r = runner(tmp_path, lipsync=ls, face=FaceKnown(True)).start(BRIEF)
    assert r.status == "done" and ls.calls == 2


def test_lipsync_limits_stop_before_paying_for_video(tmp_path, monkeypatch):
    from pipeline import registry

    tight = registry.get("lipsync", "passthrough").model_copy(update={"input_s": [2.0, 3.0]})

    class TightLipSync(fake.PassthroughLipSync):
        spec = tight

    vid = CountingVideo()
    long_brief = Brief(
        idea="a letter", lines=["This handwriting is mine, but I never wrote this letter."]
    )
    r = runner(tmp_path, video=vid, lipsync=TightLipSync(), face=FaceKnown(True)).start(long_brief)
    assert r.status == "error" and "NOT generated" in r.error
    assert vid.calls == 0


def test_lipsync_gets_only_the_shot_when_video_returns_a_longer_clip(tmp_path):
    """Kling returned 10.04 s for a 10 s request and failed the 2–10 s lipsync limit after the
    video was paid. Lipsync now gets only what the edit keeps (spec sync.md)."""
    from pipeline import registry
    from pipeline.media import probe
    from pipeline.timeline import shot_durations

    class LongerVideo(CountingVideo):
        def animate(self, image, motion, duration_s, out, seed=0):
            return super().animate(image, motion, duration_s + 0.5, out, seed)

    sent: dict[str, float] = {}

    class TightLipSync(fake.PassthroughLipSync):
        spec = registry.get("lipsync", "passthrough").model_copy(update={"input_s": [2.0, 5.0]})

        def sync(self, clip, shot_audio, out, seed=0):
            sent[out.name.split(".")[0]] = probe(clip).duration_s
            assert abs(probe(shot_audio).duration_s - probe(clip).duration_s) < 0.05
            return super().sync(clip, shot_audio, out, seed)

    rn = runner(tmp_path, video=LongerVideo(), lipsync=TightLipSync(), face=FaceKnown(True))
    r = rn.start(BRIEF)
    assert r.status == "done", r.error
    assert all(c.duration_s > 5.0 or c.shot_id == "s2" for c in r.state.clips)  # clip s1 > limit
    shots = shot_durations(r.state, rn.settings.pacing())
    assert sent.keys() == shots.keys()
    assert all(abs(sent[s] - shots[s]) < 0.05 for s in shots), (sent, shots)


@pytest.mark.parametrize("n", [1, 2])
def test_run_ids_increment(tmp_path, n):
    rn = runner(tmp_path)
    ids = [rn.start(BRIEF).run_id for _ in range(n)]
    assert len(set(ids)) == n


class CountingVideo(fake.FakeVideoGen):
    def __init__(self) -> None:
        self.calls = 0
        self.starts: list[str | None] = []  # what a shot started from: None = text, else a frame
        self.prompts: list[str] = []  # exactly what the model got

    def animate(self, image, motion, duration_s, out, seed=0):
        self.calls += 1
        self.starts.append(image.name if image else None)
        self.prompts.append(motion)
        return super().animate(image, motion, duration_s, out, seed)


def test_bad_voice_is_caught_before_video(tmp_path):
    from pipeline.config import MAX_VOICE_ATTEMPTS

    tts, vid = CountingTTS(), CountingVideo()
    r = runner(tmp_path, tts=tts, video=vid, asr=DeafASR()).start(BRIEF)
    assert r.status == "failed_voice", r.error
    assert vid.calls == 0  # nothing spent on video
    assert tts.calls == 2 * (1 + MAX_VOICE_ATTEMPTS)
    assert r.state.final is None


def test_only_failed_shot_voice_is_regenerated(tmp_path):
    class DeafOnS2Once(fake.EchoASR):
        name = "deaf-s2-once"
        seen = 0

        def transcribe(self, wav):
            if wav.stem == "s2" and self.seen == 0:
                self.seen += 1
                return "wrong words here"
            return super().transcribe(wav)

    tts = CountingTTS()
    r = runner(tmp_path, tts=tts, asr=DeafOnS2Once()).start(BRIEF)
    assert r.status == "done", r.error
    assert tts.calls == 3  # s1 once, s2 twice
    assert r.state.voice_retries == {"s2": 1}


def test_video_prompt_carries_line_and_speech_window(tmp_path):
    r = runner(tmp_path).start(BRIEF)
    prompt = Path(r.state.run_dir, "clips", "s1.prompt.txt").read_text()
    assert f'"{LINE_1}"' in prompt and "lips move only while speaking" in prompt


def test_lipsync_output_used_and_final_audio_is_ours(tmp_path):
    r = runner(tmp_path).start(BRIEF)
    run_dir = Path(r.state.run_dir)
    assert [Path(c.path).name for c in r.state.synced] == ["s1.lipsync.mp4", "s2.lipsync.mp4"]
    assert (run_dir / "voice" / "s1.shot.wav").exists()
    assert r.state.sync is not None and {s.verdict for s in r.state.sync.shots} == {"n/a"}


def test_asr_returning_numpy_numbers_does_not_break_checkpoint(tmp_path):
    """Real local ASRs return numpy numbers, echo does not. The boundary must normalize for all."""
    import numpy as np

    from pipeline.providers import Timed

    class NumpyASR(fake.EchoASR):
        def timed(self, wav):
            t = super().timed(wav)
            return Timed(t.text, np.float64(0.12), np.float64(t.speech_end_s))

    r = runner(tmp_path, asr=NumpyASR()).start(BRIEF)
    assert r.status == "done", r.error
    assert type(r.state.voices[0].speech_start_s) is float


def test_different_speakers_are_not_chained_from_the_cut_frame(tmp_path):
    """Shot 2 by another character -> a shot from text, no cut frame and no pause (keyframe.md):
    otherwise the new speaker's lips would land on the previous speaker's face."""
    from pipeline.schema import Character, Delivery, DraftShot, ScriptDraft, VoiceProfile

    class Dialogue(fake.FakeScriptWriter):
        def write(self, brief, feedback):
            cast = [
                Character(id="maya", name="Maya", gender="female",
                          voice_profile=VoiceProfile.female_young_warm,
                          look="woman in her twenties, dark curly hair, grey sweater"),
                Character(id="leo", name="Leo", gender="male",
                          voice_profile=VoiceProfile.male_young_bright,
                          look="man in his twenties, short brown hair, navy jacket"),
            ]  # fmt: skip
            shots = [
                DraftShot(character_id=c, delivery=Delivery.neutral, visual_prompt="kitchen")
                for c in ("maya", "leo")
            ]
            return ScriptDraft(title="t", style="cinematic", characters=cast, shots=shots)

    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, script=Dialogue(), video=vid)
    r = past_script(rn, rn.start(BRIEF))
    assert r.status == "done", r.error  # no pause between shots: no cut
    assert vid.starts == [None, None] and r.state.keyframes == []


def test_video_prompt_asks_for_a_visible_face_while_speaking(tmp_path):
    r = runner(tmp_path).start(BRIEF)
    prompt = Path(r.state.run_dir, "clips", "s1.prompt.txt").read_text()
    assert "face and mouth stay visible" in prompt and "no text" not in prompt.lower()
    assert "faces the camera" not in prompt  # looking away and walking are fine
