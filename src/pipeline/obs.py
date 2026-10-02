"""Trace, timings, cost and spending limits. The single output channel instead of print."""

from __future__ import annotations

import json
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.errors import GraphInterrupt
from rich.console import Console

from pipeline.config import MAX_CALLS_PER_UNIT

console = Console(stderr=True)


class BudgetExceeded(RuntimeError):
    pass


class CallLimitExceeded(RuntimeError):
    pass


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _unit(node: str, detail: dict) -> str:
    """Call-limit counter key: node + unit (a script take, a shot artifact)."""
    return f"{node}:{detail['unit']}" if detail.get("unit") else node


class Ledger:
    """Spending of all runs (runs/_ledger.jsonl): a daily cap on top of the run cap."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def spent_today(self) -> float:
        if not self.path.exists():
            return 0.0
        day = _today()
        total = 0.0
        for line in self.path.open():
            rec = json.loads(line)
            if rec["day"] == day:
                total += rec["usd"]
        return total

    def add(self, run_id: str, node: str, usd: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"day": _today(), "ts": round(time.time(), 3), "run_id": run_id, "node": node}
        with self.path.open("a") as f:
            f.write(json.dumps({**rec, "usd": round(usd, 4)}) + "\n")


class Tracer:
    def __init__(
        self,
        run_dir: Path,
        max_cost_usd: float,
        max_daily_usd: float = float("inf"),
        ledger: Ledger | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "trace.jsonl"
        self.max_cost_usd = max_cost_usd
        self.max_daily_usd = max_daily_usd
        self.ledger = ledger
        self.cost_usd, self.calls = self._restore()

    def _restore(self) -> tuple[float, Counter[str]]:
        # A run resumed after a crash keeps counting from what it already spent. The current
        # cost is the LAST record (after refunds and corrections), not the maximum.
        calls: Counter[str] = Counter()
        self.open_keys: set[str] = set()
        if not self.path.exists():
            return 0.0, calls
        cost = 0.0
        for line in self.path.open():
            rec = json.loads(line)
            cost = rec.get("cum_cost_usd", cost)
            ev, key = rec["event"], rec.get("key")
            if ev == "charge" and rec.get("cost_usd", 0) > 0:
                calls[_unit(rec["node"], rec)] += 1
                if key:
                    self.open_keys.add(key)
            elif ev == "refund":
                calls[_unit(rec["node"], rec)] -= 1
                self.open_keys.discard(key)
            elif ev in ("spent", "failed_billed"):
                self.open_keys.discard(key)
        return cost, calls

    def event(self, node: str, event: str, **detail: Any) -> None:
        rec = {
            "ts": round(time.time(), 3),
            "node": node,
            "event": event,
            "cum_cost_usd": round(self.cost_usd, 4),
            **detail,
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def charge(self, node: str, estimate_usd: float, what: str, **detail: Any) -> None:
        """Reserve the cost BEFORE a paid call. All limits are checked here, not afterwards."""
        if estimate_usd <= 0:
            self.event(node, "charge", cost_usd=0.0, what=what, **detail)
            return
        unit = _unit(node, detail)
        limit = MAX_CALLS_PER_UNIT.get(node)
        if limit is not None and self.calls[unit] >= limit:
            self.event(node, "call_limit", limit=limit, what=what, unit=detail.get("unit"))
            raise CallLimitExceeded(
                f"{node}: already {self.calls[unit]} paid calls for {detail.get('unit') or node}, "
                f"limit {limit} (config.MAX_CALLS_PER_UNIT). Looks like a loop — see trace.jsonl."
            )
        if self.cost_usd + estimate_usd > self.max_cost_usd:
            self.event(node, "budget_exceeded", estimate_usd=estimate_usd, what=what)
            raise BudgetExceeded(
                f"{node}: {what} would cost ~${estimate_usd:.2f}, already spent "
                f"${self.cost_usd:.2f} of MAX_RUN_COST_USD=${self.max_cost_usd:.2f}"
            )
        if self.ledger:
            today = self.ledger.spent_today()
            if today + estimate_usd > self.max_daily_usd:
                self.event(node, "daily_budget_exceeded", spent_today=round(today, 4))
                raise BudgetExceeded(
                    f"{node}: ${today:.2f} spent today, another ~${estimate_usd:.2f} would exceed "
                    f"MAX_DAILY_COST_USD=${self.max_daily_usd:.2f}"
                )
            self.ledger.add(self.run_dir.name, node, estimate_usd)
        self.cost_usd += estimate_usd
        self.calls[unit] += 1
        if detail.get("key"):
            self.open_keys.add(detail["key"])
        self.event(node, "charge", cost_usd=round(estimate_usd, 4), what=what, **detail)

    def refund(self, node: str, amount_usd: float, why: str, **detail: Any) -> None:
        """The provider surely did not bill (request rejected / never arrived): refund."""
        if self.ledger:
            self.ledger.add(self.run_dir.name, node, -amount_usd)
        self.cost_usd -= amount_usd
        self.calls[_unit(node, detail)] -= 1
        self.open_keys.discard(detail.get("key"))
        self.event(node, "refund", cost_usd=-round(amount_usd, 4), what=why, **detail)

    def settle(self, node: str, reserved: float, actual: float, what: str, **detail: Any) -> None:
        """The provider reported the actual cost (Claude tokens): book the difference."""
        delta = actual - reserved
        if abs(delta) < 1e-6:
            return
        if self.ledger:
            self.ledger.add(self.run_dir.name, node, delta)
        self.cost_usd += delta
        self.event(node, "settle", cost_usd=round(delta, 6), reserved=round(reserved, 6),
                   actual=round(actual, 6), what=what, **detail)  # fmt: skip

    @contextmanager
    def spend(self, node: str, provider: Any, estimate_usd: float, what: str, *, key: str,
              **detail: Any):  # fmt: skip
        """A paid provider call, the same for every model:
        1) `provider.ready()` (key, client) BEFORE the reserve — no phantom spending;
        2) reserve against the caps, unless this same work (key) is already reserved and
           unfinished (crash, job still running) — then continue WITHOUT a new reserve;
        3) on error: surely not billed -> refund; unknown -> keep the reserve;
        4) on success: the actual cost if the provider knows it (`provider.last_cost_usd`).
        """
        provider.ready()
        reserved = 0.0
        if key in self.open_keys:
            self.event(node, "resume_paid", what=what, key=key, **detail)
        else:
            self.charge(node, estimate_usd, what, key=key, **detail)
            reserved = max(estimate_usd, 0.0)
        if hasattr(provider, "last_cost_usd"):
            provider.last_cost_usd = None
        try:
            yield
        except BaseException as e:
            from pipeline.billing import not_billed

            if reserved > 0 and not_billed(e):
                self.refund(node, reserved, f"{what}: {type(e).__name__}", key=key, **detail)
            elif getattr(e, "still_running", False):
                self.event(node, "pending", what=what, key=key, **detail)
            elif reserved > 0 or key in self.open_keys:
                self.open_keys.discard(key)
                self.event(node, "failed_billed", what=what, key=key, **detail)
            raise
        actual = getattr(provider, "last_cost_usd", None)
        if actual is not None and reserved > 0:
            self.settle(node, reserved, actual, what, key=key, **detail)
        self.open_keys.discard(key)
        self.event(node, "spent", what=what, key=key, **detail)

    @contextmanager
    def stage(self, node: str):
        console.print(f"[bold cyan]▶ {node}[/]  [dim]${self.cost_usd:.2f}[/]")
        self.event(node, "start")
        t0 = time.perf_counter()
        try:
            yield
        except GraphInterrupt:
            # Waiting for a human is not an error: the graph waits, the state is checkpointed.
            self.event(node, "waiting")
            console.print(f"  [yellow]⏸[/] {node}: waiting for approval")
            raise
        except Exception as e:
            flag = {"content_rejected": True} if getattr(e, "content_rejected", False) else {}
            self.event(node, "error", error=f"{type(e).__name__}: {e}", **flag)
            raise
        dt = round(time.perf_counter() - t0, 2)
        self.event(node, "end", duration_s=dt)
        console.print(f"  [green]✓[/] {node} {dt}s  [dim]${self.cost_usd:.2f}[/]")

    def info(self, node: str, msg: str) -> None:
        console.print(f"  [dim]{node}:[/] {msg}")
