"""Idea + user lines -> Script. Spec: .claude/specs/script.md."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from pipeline import cache, pricing, runmemo
from pipeline.config import MAX_SCRIPT_FIX_ATTEMPTS
from pipeline.media import write_atomic
from pipeline.nodes import Ctx, run_path, shared_cache
from pipeline.schema import DraftMismatch, PipelineState, Script

# Bump when the prompt changes: it is part of the cache key and of the eval version.
PROMPT_VERSION = "v6"


class ScriptInvalid(RuntimeError):
    pass


def rewrite_note(rejected: Script, take: int) -> str:
    return (
        f"Rewrite #{take}: this script was rejected (by a human reviewer, or the video model's "
        "content checker refused a prompt). Write a clearly different "
        "staging (action, framing, camera); the lines stay exactly as they are.\n"
        + rejected.model_dump_json(exclude={"shots": {"__all__": {"line", "id"}}})
    )


def _on_disk(out: Path) -> Script | None:
    """A rewrite after an error starts from a clean state: the rejected script is the one on
    disk (an older schema version is simply not shown to the model)."""
    try:
        return Script.model_validate_json(out.read_text()) if out.exists() else None
    except ValidationError:
        return None


def run(state: PipelineState, ctx: Ctx) -> dict:
    out = run_path(state.run_dir, "script.json")
    write_atomic(run_path(state.run_dir, "brief.json"), state.brief.model_dump_json(indent=2))
    writer = ctx.providers.script
    take = runmemo.take(state.run_dir, "script", "script")  # "rewrite" in script approval
    key = cache.input_hash(
        state.brief.model_dump(), writer.name, PROMPT_VERSION, runmemo.salt(state.run_dir),
        *([take] if take else []),  # take 0 keeps the old key, so the shared cache stays valid
    )  # fmt: skip
    if cache.is_fresh(out, key):
        return {"script": Script.model_validate_json(out.read_text())}
    root = ctx.settings.cache_root()
    shared = shared_cache(writer)
    if shared and cache.reuse(root, "script", key, out):
        # The same input was already paid for in another run: script from the cache, $0.
        cache.mark(out, key)
        ctx.tracer.event("script", "reused", key=key)
        return {"script": Script.model_validate_json(out.read_text())}

    # Rewrite from approval: the model sees the rejected version and stages it differently.
    rejected = state.script or _on_disk(out)
    feedback = rewrite_note(rejected, take) if take and rejected else None
    for attempt in range(MAX_SCRIPT_FIX_ATTEMPTS + 1):
        try:
            est = pricing.cost(writer.spec)
            what = f"script, attempt {attempt + 1}"
            with ctx.tracer.spend("script", writer, est, what, key=f"{key}:a{attempt}",
                                  unit=f"take{take}"):  # fmt: skip
                draft = writer.write(state.brief, feedback)
            # The code inserts the lines: the model never sees them in the output schema.
            script = Script.from_draft(draft, state.brief)
            break
        except (ValidationError, DraftMismatch) as e:
            # Not a network retry but a fix: the model sees exactly what it violated.
            feedback = str(e)
            ctx.tracer.event("script", "invalid", attempt=attempt, error=feedback[:500])
    else:
        raise ScriptInvalid(f"script still invalid after {attempt + 1} attempts:\n{feedback}")

    write_atomic(out, script.model_dump_json(indent=2))
    cache.mark(out, key)
    if shared:
        cache.publish(root, "script", key, out)
    return {"script": script}
