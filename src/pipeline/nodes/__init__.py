"""Graph nodes: `run(state, ctx) -> patch`. The context holds dependencies, not state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pipeline.config import Settings
from pipeline.obs import Tracer
from pipeline.providers import Providers


@dataclass(frozen=True)
class Ctx:
    providers: Providers
    tracer: Tracer
    settings: Settings


def ask_human(payload: dict, problem: Callable[[dict], str | None]) -> dict:
    """LangGraph `interrupt()` with answer validation. A bad answer returns the same pause with
    an `error` instead of raising: LangGraph replays a node's answer on every resume, so raising
    after it would leave the run in error forever (spec pipeline.md, HITL)."""
    from langgraph.types import interrupt

    while True:
        human = interrupt(payload)
        why = problem(human) if isinstance(human, dict) else f"expected an object, got {human!r}"
        if why is None:
            return human
        payload = {**payload, "error": f"invalid decision {human}: {why}"}


def shared_cache(provider) -> bool:
    """Shared cache across runs only for paid models: its purpose is not paying twice. Free
    (local, fake) results are regenerated, otherwise a change in our code would not show.
    One rule for produce and the script node."""
    return provider.spec.price.usd > 0


def produce(
    ctx: Ctx,
    *,
    node: str,
    provider,
    key: str,
    out: Path,
    make: Callable[[], object],
    estimate_usd: float,
    what: str,
    sidecars: list[Path] = (),
    shot_id: str | None = None,
) -> bool:
    """The one path for ANY paid artifact (spec pipeline.md, "Paid calls"):
    1) fresh in this run -> nothing; 2) in the shared cache (same input) -> copy, $0;
    3) otherwise a paid call via `tracer.spend` (key before reserve, refund, actual cost),
    and the result goes to the cache for later runs. Returns True if a paid call was made."""
    from pipeline import cache

    # The call-limit unit is the artifact itself: a loop on one clip/wav is caught whatever the
    # number of shots.
    detail = {"shot_id": shot_id, "unit": out.name} if shot_id else {"unit": out.name}
    if cache.is_fresh(out, key):
        return False
    root = ctx.settings.cache_root()
    shared = shared_cache(provider)
    if shared and cache.reuse(root, node, key, out, sidecars):
        cache.mark(out, key)
        ctx.tracer.event(node, "reused", what=what, key=key, **detail)
        return False
    with ctx.tracer.spend(node, provider, estimate_usd, what, key=key, **detail):
        make()
    cache.mark(out, key)
    if shared:
        cache.publish(root, node, key, out, sidecars)
    return True


def run_path(state_run_dir: str, *parts: str) -> Path:
    p = Path(state_run_dir, *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
