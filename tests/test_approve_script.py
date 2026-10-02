"""Script approval with video prompts (spec approve_script.md) and up to 10 chained shots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import preflight
from pipeline.config import MAX_FINAL_S
from pipeline.nodes import video
from pipeline.providers import fake
from pipeline.schema import MAX_SHOTS, Brief
from tests.test_graph import BRIEF, CountingVideo, past_script, runner

TEN = [
    "I found it.", "Read it again.", "It knows me.", "Who wrote this?", "Not my hand.",
    "Wait, it's mine.", "Three days left.", "Stop yourself, it says.", "I can't breathe.",
    "Then I'll try.",
]  # fmt: skip


class CountingWriter(fake.FakeScriptWriter):
    def __init__(self) -> None:
        self.feedback: list[str | None] = []

    def write(self, brief, feedback):
        self.feedback.append(feedback)
        return super().write(brief, feedback)


def test_pause_before_any_video_shows_exactly_what_the_model_will_get(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, video=vid)
    r = rn.start(BRIEF)
    assert r.status == "waiting_approval" and r.interrupt["kind"] == "approve_script"
    assert vid.calls == 0  # no video before approval
    plan = r.interrupt["plan"]
    assert [s["start"] for s in plan["shots"]] == ["text", "cut_frame"]
    assert [s["line"] for s in plan["shots"]] == list(BRIEF.lines)  # verbatim, unchanged
    saved = json.loads((Path(r.state.run_dir) / "video_plan.json").read_text())
    assert saved == plan  # the same plan is saved as a run artifact
    r = past_script(rn, r)
    assert rn.resume(r.run_id, {"s2": "approve"}).status == "done"
    assert vid.prompts == [s["prompt"] for s in plan["shots"]]  # approved == sent


def test_rewrite_calls_the_writer_again_and_asks_again(tmp_path):
    writer, vid = CountingWriter(), CountingVideo()
    rn = runner(tmp_path, auto_approve=False, script=writer, video=vid)
    first = rn.start(BRIEF)
    again = rn.resume(first.run_id, {"script": "regenerate"})
    assert again.status == "waiting_approval" and again.interrupt["kind"] == "approve_script"
    assert len(writer.feedback) == 2 and vid.calls == 0
    # The model sees the rejected version and a request for different staging; same lines.
    assert "Rewrite #1" in writer.feedback[1] and first.state.script.title in writer.feedback[1]
    old, new = first.interrupt["plan"]["shots"], again.interrupt["plan"]["shots"]
    assert [s["prompt"] for s in old] != [s["prompt"] for s in new]
    assert [s["line"] for s in new] == list(BRIEF.lines)
    r = past_script(rn, again)
    assert rn.resume(r.run_id, {"s2": "approve"}).status == "done"


def test_same_plan_is_not_asked_twice(tmp_path):
    rn = runner(tmp_path, auto_approve=False)
    r = past_script(rn, rn.start(BRIEF))
    assert rn.resume(r.run_id, {"s2": "approve"}).status == "done"
    assert rn.start(BRIEF, run_id=r.run_id).status == "done"  # same plan and frame: no pauses


def test_changed_plan_without_approval_pays_nothing(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, video=vid)
    done = rn.start(BRIEF)
    approvals = json.loads((Path(done.state.run_dir) / "approvals.json").read_text())
    assert approvals["plan"]["by"] == "auto"  # without --review approval is automatic but recorded
    stale = done.state.model_copy(update={"plan_sha": "other-plan"})
    with pytest.raises(video.PlanNotApproved):
        video.run(stale, rn._ctx(Path(done.state.run_dir)))
    assert vid.calls == 2


def test_ten_shots_chain_like_the_second_one(tmp_path):
    vid = CountingVideo()
    rn = runner(tmp_path, auto_approve=False, video=vid)
    r = rn.start(Brief(idea="A girl reads a letter from her future self", lines=TEN))
    pauses = []
    while r.status == "waiting_approval":
        kind = r.interrupt["kind"]
        pauses.append(kind)
        ids = (
            {"script"}
            if kind == "approve_script"
            else {f["shot_id"] for f in r.interrupt["frames"]}
        )
        r = rn.resume(r.run_id, dict.fromkeys(ids, "approve"))
    assert r.status == "done", r.error
    # The script once, then each shot k from the cut frame of shot k-1, like shot 2.
    assert pauses == ["approve_script"] + ["approve_cut_frames"] * 9
    assert vid.starts == [None] + [f"s{i}.png" for i in range(2, 11)]
    assert [c.shot_id for c in r.state.clips] == [f"s{i}" for i in range(1, 11)]
    assert r.state.asr.passed and r.state.final.duration_s <= MAX_FINAL_S


def test_more_than_ten_lines_are_rejected_before_any_call():
    assert MAX_SHOTS == 10
    with pytest.raises(ValidationError, match="at most 10"):
        Brief(idea="too many", lines=[*TEN, "One line more."])


def test_preflight_warns_when_many_lines_will_not_fit(tmp_path):
    from pipeline.config import Settings
    from pipeline.providers import build

    s = Settings(_env_file=None, pipeline_providers="fake", runs_dir=tmp_path / "runs")
    with_pause = "I read it. Every word, twice."  # 6 words + a pause between sentences ≈ 3.6 s
    r = preflight.check(s, build(s), Brief(idea="a letter", lines=[with_pause] * 10))
    assert r.ok and any("video limit" in w for w in r.warnings)
    short = preflight.check(s, build(s), Brief(idea="a letter", lines=TEN))
    assert not any("video limit" in w for w in short.warnings)


def test_preflight_lipsync_limit_is_a_warning_on_the_shot_estimate(tmp_path):
    """The speech-rate estimate blocked a 9.4 s shot as "~14 s"; the exact check runs in the
    video node from the real voice, before paying for video."""
    from pipeline.config import Settings
    from pipeline.providers import build

    s = Settings(_env_file=None, pipeline_providers="fake", runs_dir=tmp_path / "runs")
    p = build(s)
    p.lipsync.spec = p.lipsync.spec.model_copy(update={"input_s": [2.0, 10.0]})  # Kling LipSync
    long = " ".join(["word"] * 30)  # ≈ 12 s by the estimate
    r = preflight.check(s, p, Brief(idea="a letter", lines=[long]))
    assert r.ok and any(f"lipsync model {p.lipsync.spec.name}" in w for w in r.warnings)
    ok = preflight.check(s, p, Brief(idea="a letter", lines=[TEN[0]]))
    assert not any("lipsync model" in w for w in ok.warnings)
