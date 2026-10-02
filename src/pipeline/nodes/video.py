"""Silent shot clips in order: shot 1 from text, shot k from the cut frame of shot k-1.
Spec: .claude/specs/video.md, keyframe.md."""

from __future__ import annotations

import math
from pathlib import Path

from pipeline import cache, pricing, runmemo
from pipeline.config import (
    MAX_CLIP_S,
    SPEECH_WINDOW_STEP_S,
)
from pipeline.media import probe, write_atomic
from pipeline.nodes import Ctx, produce, run_path
from pipeline.schema import Clip, PipelineState, Shot, ShotPlan, VideoPlan, VoiceClip
from pipeline.timeline import shot_durations


def scene_prompt(style: str, look: str, visual: str, camera: str) -> str:
    """Full scene for a shot from text. The code builds the prompt from script fields; the
    character is `look` verbatim, so it is the same in every shot. Things banned on screen go
    to the model's negative_prompt: "no text" in the positive prompt provokes text."""
    return f"{style}. {camera}, vertical 9:16. {look}. {visual}."


def request_duration(shot_s: float, allowed: tuple[float, ...]) -> float:
    """Shortest allowed duration that fits the shot; beyond the maximum assemble holds the frame."""
    need = math.ceil(shot_s * 10) / 10
    return next((d for d in sorted(allowed) if d >= need), max(allowed))


def motion_prompt(shot: Shot, voice: VoiceClip, scene: str | None = None, lead: float = 0.3) -> str:
    """Video prompt: the scene (shot from text) or action and camera (shot from a frame — the
    character and place are already there) + the line, the speech window and pauses. The model
    makes no sound: this is a hint when the character speaks; lipsync fits the lips (sync.md)."""
    head = scene or f"{shot.visual_prompt}. Camera: {shot.camera}."
    if voice.speech_start_s is None or voice.speech_end_s is None:
        return head
    step = SPEECH_WINDOW_STEP_S
    # Rounding: a new voice take must not change the prompt (and re-bill the video) over ms.
    a = math.floor((lead + voice.speech_start_s) / step) * step
    b = math.ceil((lead + voice.speech_end_s) / step) * step
    # Pause bounds rounded inwards: never claim silence where a word still sounds.
    silent = [
        f"from {lo:.1f}s to {hi:.1f}s"
        for p0, p1 in voice.pauses_s
        if (hi := math.floor((lead + p1) / step) * step)
        > (lo := math.ceil((lead + p0) / step) * step)
    ]
    pauses = f" Pauses: silent, mouth closed {', '.join(silent)}." if silent else ""
    return (
        f"{head.rstrip('.')}. The character's face and mouth stay visible while speaking the "
        f'line "{shot.line}" from {a:.1f}s to {b:.1f}s: lips move only while speaking, mouth '
        f"closed before and after.{pauses}"
    )


class OutOfModelLimits(ValueError):
    """The next model would not accept this media: stop BEFORE paying (input_s)."""


def check_lipsync_limits(
    ctx: Ctx, shot_id: str, shot_s: float, frame: Path | None, not_done: str
) -> None:
    """Lipsync gets only the shot (spec sync.md), so the limits apply to the shot duration,
    which the code knows before the video; a model clip can be longer than requested. Shot 1 has
    no frame before its video, so it is always checked, conservatively."""
    lip = ctx.providers.lipsync.spec
    if not lip.input_s or (frame is not None and ctx.providers.face.has_face(frame) is False):
        return  # no limits, or lipsync will be skipped (no face)
    lo, hi = lip.input_s
    if not lo <= shot_s <= hi:
        raise OutOfModelLimits(
            f"shot {shot_id} {shot_s:.1f} s is outside the lipsync model {lip.name} limits "
            f"({lo:.0f}–{hi:.0f} s). {not_done}: shorten the line or choose another "
            "LIPSYNC_MODEL."
        )


class PlanNotApproved(RuntimeError):
    """The video plan changed after approval: no paid call (spec approve_script.md)."""


def plan(state: PipelineState, ctx: Ctx) -> VideoPlan:
    """What exactly goes to the video model: prompt, start and duration of each shot. One
    function for the script approval and for the request, so approved == sent."""
    assert state.script
    voices = {v.shot_id: v for v in state.voices}
    gen, lip = ctx.providers.video.spec, ctx.providers.lipsync.spec
    pacing = ctx.settings.pacing()
    # Timeline before any payment: a too long video (TooLong) stops here, not after shot 1.
    shots_s = shot_durations(state, pacing)
    shots = state.script.shots
    out: list[ShotPlan] = []
    for i, shot in enumerate(shots):
        from_frame = i > 0 and chained(shots[i - 1], shot)
        clip_s = request_duration(shots_s[shot.id], tuple(gen.durations_s))
        char = state.script.character(shot.character_id)
        scene = None if from_frame else scene_prompt(state.script.style, char.look,
                                                     shot.visual_prompt, shot.camera)  # fmt: skip
        out.append(
            ShotPlan(
                shot_id=shot.id,
                character_id=shot.character_id,
                line=shot.line,
                delivery=shot.delivery,
                start="cut_frame" if from_frame else "text",
                source_shot=shots[i - 1].id if from_frame else None,
                shot_s=shots_s[shot.id],
                clip_s=clip_s,
                prompt=motion_prompt(shot, voices[shot.id], scene, pacing.lead_s),
            )  # fmt: skip
        )
    cost = sum(pricing.cost(gen, seconds=p.clip_s) + pricing.cost(lip, seconds=p.shot_s)
               for p in out)  # fmt: skip
    return VideoPlan(
        model=ctx.providers.video.name,
        negative_prompt=gen.args.get("negative_prompt"),
        cost_usd=round(cost, 4),
        shots=out,
    )


def plan_sha(p: VideoPlan) -> str:
    return cache.input_hash(p.model_dump())


def run(state: PipelineState, ctx: Ctx) -> dict:
    p = plan(state, ctx)
    if plan_sha(p) != state.plan_sha:
        raise PlanNotApproved(
            "the video plan changed after approval (new script or voice take) — approve it again"
        )
    frames = {k.shot_id: k for k in state.keyframes}
    gen = ctx.providers.video
    clips: list[Clip] = []
    for sp in p.shots:
        start: Path | None = None
        if sp.start == "cut_frame":
            k = frames.get(sp.shot_id)
            prev = Path(clips[-1].path)
            if not (k and k.approved and k.source_sha == cache.file_hash(prev)):
                break  # shot k only from a fresh, approved cut frame (keyframe -> approve)
            start = Path(k.path)
        duration = sp.clip_s
        if duration > MAX_CLIP_S:
            raise ValueError(f"clip {sp.shot_id}: {duration}s > MAX_CLIP_S={MAX_CLIP_S}")
        check_lipsync_limits(ctx, sp.shot_id, sp.shot_s, start, "Video was NOT generated")
        out = run_path(state.run_dir, "clips", f"{sp.shot_id}.mp4")
        motion = sp.prompt
        seed = runmemo.seed_offset(state.run_dir, "video", sp.shot_id)
        key = cache.input_hash(
            cache.file_hash(start) if start else None, motion, duration, gen.name,
            runmemo.take(state.run_dir, "video", sp.shot_id), runmemo.salt(state.run_dir),
        )  # fmt: skip
        prompt_file = out.with_suffix(".prompt.txt")

        def make(start=start, motion=motion, duration=duration, out=out, seed=seed,
                 prompt_file=prompt_file):  # fmt: skip
            gen.animate(start, motion, duration, out, seed)
            write_atomic(prompt_file, motion)

        produce(ctx, node="video", provider=gen, key=key, out=out, make=make,
                estimate_usd=pricing.cost(gen.spec, seconds=duration),
                what=f"clip {sp.shot_id} {duration:.0f}s from {'cut frame' if start else 'text'}",
                sidecars=[out.with_name(out.name + ".url"), prompt_file],
                shot_id=sp.shot_id)  # fmt: skip
        clips.append(Clip(shot_id=sp.shot_id, path=str(out), duration_s=probe(out).duration_s))
    return {"clips": clips}


def chained(prev: Shot, shot: Shot) -> bool:
    """A shot continues from the cut frame only for the same speaker (spec keyframe.md)."""
    return prev.character_id == shot.character_id


def route(state: PipelineState) -> str:
    """All shots done -> lipsync; otherwise the cut frame for the next shot."""
    assert state.script
    return "lipsync" if len(state.clips) == len(state.script.shots) else "keyframe"
