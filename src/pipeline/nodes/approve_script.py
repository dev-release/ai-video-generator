"""HITL before the first video: the script + the exact prompts the video model will get.
Spec: .claude/specs/approve_script.md.

Approval is bound to the plan hash (`approvals.json["plan"]`): the same plan is not asked twice,
a changed one (new script, new voice take) never passes without a human.
"""

from __future__ import annotations

from typing import Literal

from pipeline import runmemo
from pipeline.media import write_atomic
from pipeline.nodes import Ctx, ask_human, run_path
from pipeline.nodes.video import plan, plan_sha
from pipeline.schema import PipelineState

Decision = Literal["approve", "regenerate"]
KEY = "plan"  # key in approvals.json; shot ids are s1…s10, no clash


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    p = plan(state, ctx)
    sha = plan_sha(p)
    write_atomic(run_path(state.run_dir, "video_plan.json"), p.model_dump_json(indent=2))
    if runmemo.approved_sha(state.run_dir, KEY) == sha:
        return {"plan_sha": sha}  # this exact plan was already approved (by a human or auto)
    if ctx.settings.auto_approve:
        decision: Decision = "approve"
    else:
        # The graph stops here; state is checkpointed. Resume: Command(resume={"script": …}).
        human = ask_human(
            {"kind": "approve_script", "script": state.script.model_dump(),
             "plan": p.model_dump(), "next_cost_usd": p.cost_usd},
            lambda h: None if set(h) == {"script"} and h["script"] in ("approve", "regenerate")
            else 'expected {"script": "approve" | "regenerate"}',
        )  # fmt: skip
        decision = human["script"]
    ctx.tracer.event("approve_script", "decision", decision=decision, plan=sha)
    if decision == "regenerate":
        runmemo.bump_take(state.run_dir, "script", "script")
        return {"plan_sha": None}
    runmemo.record_approval(
        state.run_dir, KEY, sha, "auto" if ctx.settings.auto_approve else "human"
    )
    return {"plan_sha": sha}


def route(state: PipelineState) -> str:
    """Approved -> video; rewrite -> a new script take (then voice and approval again)."""
    return "video" if state.plan_sha else "script"
