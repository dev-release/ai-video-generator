"""LangGraph: nodes, edges, SQLite checkpoint. One core for CLI and web UI. Spec: pipeline.md."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import ValidationError

from pipeline import schema
from pipeline.config import Settings
from pipeline.media import write_atomic
from pipeline.nodes import (
    Ctx,
    approve,
    approve_script,
    assemble,
    keyframe,
    lipsync,
    script,
    sync_check,
    verify,
    video,
    voice,
    voice_check,
)
from pipeline.obs import Ledger, Tracer
from pipeline.providers import Providers, build
from pipeline.schema import Brief, PipelineState

NODES: dict[str, Callable[[PipelineState, Ctx], dict]] = {
    "script": script.run,
    "voice": voice.run,
    "voice_check": voice_check.run,
    "approve_script": approve_script.run,
    "video": video.run,
    "keyframe": keyframe.run,
    "approve": approve.run,
    "lipsync": lipsync.run,
    "assemble": assemble.run,
    "verify": verify.run,
    "sync_check": sync_check.run,
}
ORDER = list(NODES)
# Graph steps per invoke: ~3 per shot (video -> keyframe -> approve) x MAX_SHOTS + the rest + loops.
RECURSION_LIMIT = 4 * schema.MAX_SHOTS + 40

# Explicit allowlist of checkpoint types; without it LangGraph deserializes anything.
_CHECKPOINT_TYPES = [
    ("pipeline.schema", n)
    for n in dir(schema)
    if isinstance(getattr(schema, n), type) and getattr(schema, n).__module__ == "pipeline.schema"
]

Status = Literal[
    "running", "waiting_approval", "done", "failed_voice", "failed_verify", "failed_sync", "error"
]


def after_verify(state: PipelineState) -> str:
    # A failed final gate never re-runs paid work: the voice already passed the audio gate, and
    # a new voice would drag in new video and lipsync. The run stops with a diff; a human can
    # regenerate a shot explicitly (UI / --regen).
    return "sync_check" if state.asr and state.asr.passed else END


def build_graph(ctx: Ctx, checkpointer: SqliteSaver | None = None):
    g = StateGraph(PipelineState)
    for name, fn in NODES.items():

        def node(state: PipelineState, _fn=fn, _name=name) -> dict:
            with ctx.tracer.stage(_name):
                return _fn(state, ctx)

        g.add_node(name, node)
    g.add_edge(START, "script")
    g.add_edge("script", "voice")
    g.add_edge("voice", "voice_check")
    # Audio gate before video: a bad voice costs a new take, not a clip.
    g.add_conditional_edges("voice_check", voice_check.route, ["approve_script", "voice", END])
    # Script and video prompts are approved before the first paid video; rewrite -> new script take.
    g.add_conditional_edges("approve_script", approve_script.route, ["video", "script"])
    # Shots in order: shot 1 from text -> cut frame -> [approve] -> shot 2 from it -> … -> lipsync.
    g.add_conditional_edges("video", video.route, ["keyframe", "lipsync"])
    g.add_edge("keyframe", "approve")
    g.add_edge("approve", "video")
    g.add_edge("lipsync", "assemble")
    g.add_edge("assemble", "verify")
    g.add_conditional_edges("verify", after_verify, ["sync_check", END])
    g.add_edge("sync_check", END)
    return g.compile(checkpointer=checkpointer)


def new_run_id(runs_dir: Path) -> str:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    taken = {p.name for p in runs_dir.glob(f"{day}-*")} if runs_dir.exists() else set()
    n = 1
    while f"{day}-{n:02d}" in taken:
        n += 1
    return f"{day}-{n:02d}"


@dataclass
class RunResult:
    run_id: str
    status: Status
    state: PipelineState | None
    interrupt: dict[str, Any] | None = None
    error: str | None = None


class LegacyRun(KeyError):
    """Checkpoint from an older schema version (e.g. a removed voice profile): viewable from
    artifacts, not resumable — and not a 500 in the UI."""


class Runner:
    """Starts and resumes runs. A run's state lives in the checkpoint (thread_id = run_id)."""

    def __init__(self, settings: Settings, providers: Providers | None = None) -> None:
        self.settings = settings
        self.providers = providers or build(settings)
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the UI resumes a run from another thread than it started on.
        conn = sqlite3.connect(settings.runs_dir / "checkpoints.sqlite", check_same_thread=False)
        serde = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_TYPES)
        self.checkpointer = SqliteSaver(conn, serde=serde)
        self.ledger = Ledger(settings.runs_dir / "_ledger.jsonl")

    def _ctx(self, run_dir: Path) -> Ctx:
        tracer = Tracer(
            run_dir, self.settings.max_run_cost_usd, self.settings.max_daily_cost_usd, self.ledger
        )
        return Ctx(providers=self.providers, tracer=tracer, settings=self.settings)

    def _invoke(self, run_id: str, payload: Any) -> RunResult:
        run_dir = self.settings.runs_dir / run_id
        graph = build_graph(self._ctx(run_dir), self.checkpointer)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
        try:
            graph.invoke(payload, config)
        # Core boundary for UI/CLI: an error becomes a status (details in trace.jsonl).
        except Exception as e:  # noqa: BLE001
            snap = graph.get_state(config)
            state = PipelineState.model_validate(snap.values) if snap.values else None
            return RunResult(run_id, "error", state, error=f"{type(e).__name__}: {e}")
        return self.status(run_id, graph)

    def start(self, brief: Brief, run_id: str | None = None) -> RunResult:
        run_id = run_id or new_run_id(self.settings.runs_dir)
        run_dir = self.settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        # Which models made this run — to compare versions and models in evals and the UI.
        write_atomic(run_dir / "models.json", json.dumps(self.providers.models(), indent=2))
        # Pacing is configuration; the values used are an artifact of the run.
        write_atomic(run_dir / "pacing.json", self.settings.pacing().model_dump_json(indent=2))
        state = PipelineState(run_id=run_id, run_dir=str(run_dir), brief=brief)
        return self._invoke(run_id, state)

    def resume(self, run_id: str, decisions: dict[str, str] | None = None) -> RunResult:
        """Resume after an approval or a failure. None = from the last checkpoint as is."""
        return self._invoke(run_id, Command(resume=decisions) if decisions else None)

    def cost(self, run_id: str) -> float:
        return self._ctx(self.settings.runs_dir / run_id).tracer.cost_usd

    def status(self, run_id: str, graph=None) -> RunResult:
        graph = graph or build_graph(self._ctx(self.settings.runs_dir / run_id), self.checkpointer)
        snap = graph.get_state({"configurable": {"thread_id": run_id}})
        if not snap.values:
            raise KeyError(f"run {run_id} is not in the checkpoints")
        try:
            state = PipelineState.model_validate(snap.values)
        # An old checkpoint can fail to load in several ways (a dict where a model is expected).
        except (ValidationError, AttributeError, TypeError) as e:
            raise LegacyRun(
                f"{run_id}: made by an older pipeline version ({type(e).__name__})"
            ) from e
        interrupts = [i.value for t in snap.tasks for i in t.interrupts]
        if interrupts:
            return RunResult(run_id, "waiting_approval", state, interrupt=interrupts[0])
        if snap.next:
            return RunResult(run_id, "running", state)
        if (
            state.final is None
            and state.voice_checks
            and not all(c.passed for c in state.voice_checks)
        ):
            return RunResult(run_id, "failed_voice", state)
        if not (state.asr and state.asr.passed):
            return RunResult(run_id, "failed_verify", state)
        if state.sync and not state.sync.passed:
            return RunResult(run_id, "failed_sync", state)
        return RunResult(run_id, "done", state)
