"""Cut frame: shot k starts from the frame where the edit cuts shot k-1. Spec: keyframe.md.

No separate image generation: shot 1 starts from text, shot k continues from exactly the frame
shot k-1 ends on in the final video. The frame is extracted by code, $0."""

from __future__ import annotations

from pathlib import Path

from pipeline import cache
from pipeline.media import extract_frame, probe
from pipeline.nodes import Ctx, run_path
from pipeline.nodes.assemble import FPS
from pipeline.nodes.video import chained
from pipeline.schema import Keyframe, PipelineState
from pipeline.timeline import shot_durations


def cut_at(shot_s: float, clip_s: float) -> float:
    """Second of the last frame of a shot in the final video: assemble cuts the clip at the shot
    duration (or holds the last frame if the clip is shorter)."""
    return round(max(0.0, min(shot_s, clip_s) - 1 / FPS), 3)


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    shots = state.script.shots
    clips = {c.shot_id: c for c in state.clips}
    durations = shot_durations(state, ctx.settings.pacing())
    old = {k.shot_id: k for k in state.keyframes}
    frames: list[Keyframe] = []
    for prev, shot in zip(shots, shots[1:], strict=False):
        if not chained(prev, shot):
            continue  # another speaker: the shot starts from text, no cut frame
        clip = clips.get(prev.id)
        if clip is None:
            break  # a cut frame needs the finished previous shot
        src = Path(clip.path)
        sha = cache.file_hash(src)
        at = cut_at(durations[prev.id], probe(src).duration_s)
        out = run_path(state.run_dir, "keyframes", f"{shot.id}.png")
        key = cache.input_hash(sha, at)
        if not cache.is_fresh(out, key):
            extract_frame(src, at, out)
            cache.mark(out, key)
        was = old.get(shot.id)
        frames.append(
            Keyframe(
                shot_id=shot.id,
                path=str(out),
                prompt=f"frame of {prev.id} at {at:.2f} s → start of {shot.id}",
                # The approval lives while the frame is the same (same source clip).
                approved=bool(was and was.approved and was.source_sha == sha),
                source_shot=prev.id,
                source_sha=sha,
                at_s=at,
            )
        )
        ctx.tracer.event("keyframe", "cut_frame", shot_id=shot.id, source=prev.id, at_s=at)
    return {"keyframes": frames}
