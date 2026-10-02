"""Video length = the lines + short pauses, not padded with silence. Spec: assemble.md.

A shot = the phrase + a short pause before and after + pauses between sentences; no long
silence unless the text asks for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import registry
from pipeline.config import Pacing, Settings
from pipeline.graph import Runner
from pipeline.media import probe, silent_runs
from pipeline.nodes.video import request_duration
from pipeline.schema import Brief
from pipeline.timeline import TooLong, timeline

P = Pacing()  # default pacing
PADS = P.lead_s + P.tail_s
CUT = P.tail_s + P.lead_s  # silence at a cut: tail + the next shot's lead
NOISE = 0.15  # word edges and compression


def test_shot_is_phrase_plus_short_thinking_beats_without_filler():
    # A real run: speech 1.34 and 1.73 s used to give [2.0, 6.0] (4 s of silence at the end).
    assert timeline([1.34, 1.73]) == [
        max(round(1.34 + PADS, 3), P.min_shot_s),
        round(1.73 + PADS, 3),
    ]
    assert timeline([3.29, 2.51]) == [round(3.29 + PADS, 3), round(2.51 + PADS, 3)]
    assert sum(timeline([1.34])) < 8  # a short line, a short video, no tail


def test_too_long_is_refused():
    with pytest.raises(TooLong):
        timeline([20.0, 12.0])


@pytest.mark.parametrize("voice_s", [0.8, 1.5, 2.6, 3.4, 7.9, 14.0])
def test_paid_clip_exceeds_the_shot_only_by_model_rounding(voice_s):
    """Kling bills whole seconds from 3 to 15: beyond the shot we pay < 1 s (or up to 3 s)."""
    allowed = tuple(registry.get("video", "kling-v3-std").durations_s)
    shot = timeline([voice_s])[0]
    clip = request_duration(shot, allowed)
    assert clip >= shot
    assert clip - shot < 1 or clip == min(allowed)


def run(tmp_path: Path, lines: list[str], idea: str = "A girl in a quiet park"):
    s = Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )
    r = Runner(s).start(Brief(idea=idea, lines=lines))
    assert r.status == "done", r.error
    assert r.state and r.state.final
    return r.state


def longest_silence(path: str) -> float:
    return max((b - a for a, b in silent_runs(Path(path))), default=0.0)


def test_owner_short_lines_give_a_short_video_without_dead_air(tmp_path):
    state = run(tmp_path, ["it is a perfect day", "want to see it tomorrow also"])
    shots = sum(timeline([v.duration_s for v in state.voices]))
    final = probe(Path(state.final.path)).duration_s
    assert abs(final - shots) < 0.1 and final < 8  # video = sum of shots, no tail
    assert longest_silence(state.final.path) <= CUT + NOISE
    paid = sum(probe(Path(c.path)).duration_s for c in state.clips)
    assert paid - final < len(state.clips) * 3  # at most the model's rounding per shot


def test_silence_is_only_where_the_text_asks_for_it(tmp_path):
    state = run(tmp_path, ["Wait... it was you all this time."])
    longest = longest_silence(state.final.path)
    # "..." asks for a pause: it is there, and no longer silence.
    assert P.pause_ellipsis_s - NOISE <= longest <= P.pause_ellipsis_s + NOISE


def test_pacing_is_a_setting_not_code():
    """Pacing is configuration: durations change without code."""
    slow = Settings(_env_file=None, pace_lead_s=0.8, pace_tail_s=0.5).pacing()
    assert timeline([2.0], slow) == [round(0.8 + 2.0 + 0.5, 3)]
    # min_final_s=8 restores "8–30 s" from the task: a freeze frame at the end, by config.
    assert sum(timeline([1.0], Pacing(min_final_s=8))) == 8
    longer = Settings(_env_file=None, pause_sentence_s=1.5).pacing()
    from pipeline.nodes.voice import phrases

    assert [p.pause_after_s for p in phrases("Stop. Now.", longer)] == [1.5, 0.0]
    with pytest.raises(ValueError):
        Settings(_env_file=None, pace_lead_s=9).pacing()  # bounds fail before a run


def test_run_records_its_pacing(tmp_path):
    s = Settings(_env_file=None, pipeline_providers="fake", auto_approve=True,
                 runs_dir=tmp_path / "runs", pace_lead_s=0.5)  # fmt: skip
    r = Runner(s).start(Brief(idea="A girl", lines=["I never wrote this letter."]))
    assert r.status == "done", r.error
    import json

    saved = json.loads((Path(r.state.run_dir) / "pacing.json").read_text())
    assert saved["lead_s"] == 0.5
    v = r.state.voices[0]
    assert (
        abs(probe(Path(r.state.final.path)).duration_s - max(0.5 + v.duration_s + 0.3, 2.0)) < 0.1
    )
