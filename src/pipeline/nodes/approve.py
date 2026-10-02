"""HITL between shots: approve the cut frame before paying for the next shot. A LangGraph
interrupt, the same for CLI and UI; the human sees the end of the previous clip next to the frame.

Approval is bound to the frame content (`approvals.json`, hash): the same frame is not asked
twice, a new one never passes without a human. "Regenerate" = a new take of the source video.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pipeline import cache, pricing, runmemo
from pipeline.nodes import Ctx, ask_human
from pipeline.nodes.video import request_duration
from pipeline.schema import Keyframe, PipelineState
from pipeline.timeline import shot_durations

Decision = Literal["approve", "regenerate"]


def next_cost(state: PipelineState, ctx: Ctx, frames: list[Keyframe]) -> float:
    """What approving will cost: video + lipsync of the shots that start from these frames."""
    shots_s = shot_durations(state, ctx.settings.pacing())
    video, lip = ctx.providers.video.spec, ctx.providers.lipsync.spec
    total = 0.0
    for k in frames:
        clip_s = request_duration(shots_s[k.shot_id], tuple(video.durations_s))
        total += pricing.cost(video, seconds=clip_s)
        if ctx.providers.face.has_face(Path(k.path)) is not False:
            total += pricing.cost(lip, seconds=shots_s[k.shot_id])  # lipsync gets only the shot
    return round(total, 4)


def run(state: PipelineState, ctx: Ctx) -> dict:
    pending = [k for k in state.keyframes if not k.approved]
    if not pending:
        return {}
    run_dir = state.run_dir
    decisions: dict[str, Decision] = {}
    for k in pending:
        if runmemo.approved_sha(run_dir, k.shot_id) == cache.file_hash(Path(k.path)):
            decisions[k.shot_id] = "approve"  # the human already approved this exact frame
    ask = [k for k in pending if k.shot_id not in decisions]
    if ask and ctx.settings.auto_approve:
        decisions |= {k.shot_id: "approve" for k in ask}
    elif ask:
        # The graph stops here; state is checkpointed. Resume: Command(resume={shot_id: …}).
        frames = [k.model_dump() for k in ask]
        ids = {k.shot_id for k in ask}
        human = ask_human(
            {"kind": "approve_cut_frames", "frames": frames,
             "next_cost_usd": next_cost(state, ctx, ask)},
            lambda h: None if set(h) == ids and set(h.values()) <= {"approve", "regenerate"}
            else f"expected approve | regenerate for {sorted(ids)}",
        )  # fmt: skip
        decisions |= human
    frames_out = []
    by = "auto" if ctx.settings.auto_approve else "human"
    for k in state.keyframes:
        d = decisions.get(k.shot_id)
        if d == "regenerate" and k.source_shot:
            # The frame derives from the previous shot's video: retake that video.
            runmemo.bump_take(run_dir, "video", k.source_shot)
        elif d == "approve" and not k.approved:
            runmemo.record_approval(run_dir, k.shot_id, cache.file_hash(Path(k.path)), by)
        frames_out.append(k.model_copy(update={"approved": k.approved or d == "approve"}))
    ctx.tracer.event("approve", "decision", decisions=decisions)
    return {"keyframes": frames_out}
