"""Spec: .claude/specs/sync.md — sync metrics on synthetic series."""

from __future__ import annotations

import math

import pytest

from pipeline import sync
from pipeline.config import SYNC_FPS

FRAME_MS = 1000 / SYNC_FPS


def speech_env(n: int = 150, start: int = 30, end: int = 110) -> list[float]:
    # Speech with ~4 Hz "syllables" inside the window, silence outside.
    return [
        round(0.5 + 0.5 * math.sin(2 * math.pi * 4 * i / SYNC_FPS), 3) if start <= i < end else 0.0
        for i in range(n)
    ]


def shifted(xs: list[float], k: int) -> list[float | None]:
    """The mouth lags the audio by k frames."""
    return [0.0] * k + [0.8 * x for x in xs[: len(xs) - k]]


def test_in_sync_mouth_is_ok():
    env = speech_env()
    r = sync.analyze("s1", shifted(env, 0), env, [(30, 110)])
    assert r.verdict == "ok" and r.best_lag_ms == 0 and r.corr > 0.9


@pytest.mark.parametrize("k", [3, 6])
def test_lag_is_measured_in_ms(k):
    env = speech_env()
    lag, corr = sync.best_lag(shifted(env, k), env, 12)
    assert lag == k and corr > 0.9


def test_large_lag_is_off():
    env = speech_env()
    r = sync.analyze("s1", shifted(env, 8), env, [(30, 110)])  # ~267 ms
    assert r.verdict == "off" and abs(r.best_lag_ms - 8 * FRAME_MS) < 1


def test_mouth_moving_in_silence_is_off():
    env = speech_env()
    jaw = [0.6 if i < 30 or i >= 110 else 0.1 for i in range(len(env))]
    r = sync.analyze("s1", jaw, env, [(30, 110)])
    assert r.verdict == "off" and r.mouth_speech_ratio < 1


def test_no_face_is_not_applicable():
    env = speech_env()
    r = sync.analyze("s1", [None] * len(env), env, [(30, 110)])
    assert r.verdict == "n/a" and r.face_ratio == 0


def test_partial_face_frames_are_ignored_not_zeroed():
    env = speech_env()
    jaw = shifted(env, 0)
    jaw = [None if i % 5 == 0 else v for i, v in enumerate(jaw)]
    r = sync.analyze("s1", jaw, env, [(30, 110)])
    assert r.verdict == "ok" and 0.7 < r.face_ratio < 0.9


def test_mouth_opening_from_lip_landmarks():
    """Mouth openness = inner lip gap (62-66) / mouth width (60-64), iBUG-68."""
    import numpy as np

    from pipeline.providers.face import mouth_open, normalized

    pts = np.zeros((68, 2))
    pts[60], pts[64] = (0, 0), (40, 0)  # mouth corners
    pts[62], pts[66] = (20, -5), (20, 5)  # lips 10 apart
    assert abs(mouth_open(pts) - 0.25) < 1e-6
    assert normalized([0.1, None, 0.3, 0.2]) == [0.0, None, 1.0, 0.5]  # no face is skipped


def test_pauses_are_silence_for_the_mouth_check():
    assert sync.speech_frames(1.0, 4.0, [(2.0, 3.0)], 10) == [(10, 20), (30, 40)]
    env = speech_env()
    jaw = [0.6 if 60 <= i < 80 else 0.1 for i in range(len(env))]  # the mouth moves in a pause
    speech = sync.speech_frames(1.0, 110 / SYNC_FPS, [(2.0, 80 / SYNC_FPS)], SYNC_FPS)
    assert sync.mouth_speech_ratio(jaw, speech) < 1


def test_calibration_controls_on_a_fake_run(tmp_path):
    """Controls with a known answer: a synced video is ok; the same video shifted by 0.4 s
    against the audio is off. A gate that cannot tell them apart measures the wrong thing."""
    from pipeline.config import Settings
    from pipeline.graph import Runner
    from pipeline.schema import Brief

    s = Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )
    r = Runner(s).start(
        Brief(idea="A girl", lines=["This handwriting is mine, but I never wrote it."])
    )
    assert r.status == "done", r.error
    shot = r.state.sync.shots[0]
    assert shot.verdict == "ok", shot
    speech = [(i, i + 1) for i, v in enumerate(shot.env) if v > 0.05]
    for k in (12, -12):  # 0.4 s
        m = (
            shot.mouth[k:] + [shot.mouth[-1]] * k
            if k > 0
            else [shot.mouth[0]] * -k + shot.mouth[:k]
        )
        assert sync.analyze("s1", m, shot.env, speech).verdict == "off", k


@pytest.mark.parametrize(("nose_x", "turned"), [(50.0, False), (58.0, False), (75.0, True)])
def test_strongly_turned_face_is_not_measured(nose_x, turned):
    """A 3/4 look is measured, a profile is not: LBF is unreliable there, so the frame is skipped
    instead of a false "out of sync" (spec sync.md). YuNet points: eyes (40, 60), nose nose_x."""
    import numpy as np

    from pipeline.config import SYNC_MAX_YAW
    from pipeline.providers.face import head_yaw

    face = np.zeros(15)
    face[4:6], face[6:8], face[8:10] = (40, 50), (60, 50), (nose_x, 60)
    assert (abs(head_yaw(face)) > SYNC_MAX_YAW) is turned
