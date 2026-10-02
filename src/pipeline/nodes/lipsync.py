"""Lips to OUR shot audio. Spec: .claude/specs/sync.md.

Only the shot goes to the model — the part of the clip the edit keeps (a video model can return
a longer clip than requested) — with shot audio of the same length. The model moves only the
lips; assemble drops its audio. No face on the frame -> lipsync is skipped (nothing to pay for).
"""

from __future__ import annotations

from pathlib import Path

from pipeline import cache, pricing, runmemo
from pipeline.media import extract_frame, probe, trim_audio, trim_video
from pipeline.nodes import Ctx, produce, run_path
from pipeline.nodes.video import check_lipsync_limits
from pipeline.schema import Clip, PipelineState
from pipeline.timeline import ensure_shot_audio, shot_durations


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    ls = ctx.providers.lipsync
    pacing = ctx.settings.pacing()
    audio = ensure_shot_audio(state, pacing)
    shots_s = shot_durations(state, pacing)
    clips = {c.shot_id: c for c in state.clips}
    voices = {v.shot_id: v for v in state.voices}
    synced: list[Clip] = []
    for shot in state.script.shots:
        clip = Path(clips[shot.id].path)
        clip_s = round(probe(clip).duration_s, 3)
        # Face check on the clip frame in the middle of the speech.
        v = voices[shot.id]
        mid = pacing.lead_s + ((v.speech_start_s or 0.0) + (v.speech_end_s or v.duration_s)) / 2
        face_png = run_path(state.run_dir, "clips", f"{shot.id}.face.png")
        face_key = cache.input_hash(cache.file_hash(clip), mid)
        if not cache.is_fresh(face_png, face_key):
            extract_frame(clip, min(mid, max(clip_s - 0.1, 0.0)), face_png)
            cache.mark(face_png, face_key)
        if ctx.providers.face.has_face(face_png) is False:
            ctx.tracer.event("lipsync", "skipped", shot_id=shot.id, why="no face in the clip")
            synced.append(clips[shot.id])
            continue
        send_s = round(min(shots_s[shot.id], clip_s), 3)
        check_lipsync_limits(ctx, shot.id, send_s, face_png, "Lipsync was NOT started")
        # Speech must fit in what we send, otherwise the lips cannot keep up.
        if pacing.lead_s + voices[shot.id].duration_s > send_s + 0.05:
            raise ValueError(
                f"{shot.id}: speech is longer than the {clip_s} s clip — lipsync not started"
            )
        lip_clip = run_path(state.run_dir, "clips", f"{shot.id}.lip.mp4")
        src_key = cache.input_hash(cache.file_hash(clip), send_s)
        if not cache.is_fresh(lip_clip, src_key):
            trim_video(clip, send_s, lip_clip)
            cache.mark(lip_clip, src_key)
        lip_s = round(probe(lip_clip).duration_s, 3)
        lip_audio = run_path(state.run_dir, "voice", f"{shot.id}.lip.wav")
        lip_key = cache.input_hash(cache.file_hash(audio[shot.id]), lip_s)
        if not cache.is_fresh(lip_audio, lip_key):
            trim_audio(audio[shot.id], lip_s, lip_audio)
            cache.mark(lip_audio, lip_key)
        out = run_path(state.run_dir, "clips", f"{shot.id}.lipsync.mp4")
        # Key from the source clip and length, not from the re-encoded file.
        key = cache.input_hash(
            src_key, cache.file_hash(lip_audio), ls.name,
            runmemo.take(state.run_dir, "lipsync", shot.id), runmemo.salt(state.run_dir),
        )  # fmt: skip
        seed = runmemo.seed_offset(state.run_dir, "lipsync", shot.id)
        produce(ctx, node="lipsync", provider=ls, key=key, out=out,
                make=lambda lip_clip=lip_clip, lip_audio=lip_audio, out=out, seed=seed:
                ls.sync(lip_clip, lip_audio, out, seed),
                estimate_usd=pricing.cost(ls.spec, seconds=lip_s), what=f"lipsync {shot.id}",
                sidecars=[out.with_name(out.name + ".url")], shot_id=shot.id)  # fmt: skip
        synced.append(Clip(shot_id=shot.id, path=str(out), duration_s=probe(out).duration_s))
    return {"synced": synced}
