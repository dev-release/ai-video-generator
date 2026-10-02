"""Web API for the UI: start, state, SSE trace, approval, partial regeneration. Spec: specs/ui.md.

A thin shell over graph.Runner — the same logic as the CLI. Run state is rebuilt from artifacts
and trace.jsonl, so the UI can show any run, including one copied from another machine.

    uvicorn --factory pipeline.api:build_app --reload      # or make ui
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline import preflight, registry, runmemo
from pipeline.config import ROOT, Settings
from pipeline.config import settings as load_settings
from pipeline.graph import ORDER, LegacyRun, Runner, new_run_id
from pipeline.media import write_atomic
from pipeline.schema import (
    MAX_SHOTS,
    MAX_WORDS_PER_SHOT,
    MAX_WORDS_TOTAL,
    MIN_WORDS_PER_SHOT,
    AsrCheck,
    Brief,
    Delivery,
    PipelineState,
    SyncCheck,
    VideoPlan,
    VoiceCheck,
)

Mode = Literal["fake", "real"]
NodeStatus = Literal["pending", "running", "done", "error", "waiting"]
UI_DIST = ROOT / "ui" / "dist"


class StartRequest(Brief):
    """Same line rules as the CLI (Brief): FastAPI returns 422 with the field."""

    mode: Mode = "fake"
    auto_approve: bool = False
    # Deliberately fresh: do not take paid artifacts from the shared cache.
    fresh: bool = False


class ApproveRequest(BaseModel):
    decisions: dict[str, Literal["approve", "regenerate"]]


class RegenerateRequest(BaseModel):
    node: Literal["voice", "video", "lipsync"]
    shot_id: str = Field(pattern=r"^s[0-9]{1,2}$")


class Rejected(BaseModel):
    """A model's content checker refused a shot: resuming repeats the same request, so the UI
    offers a new take of that shot or a rewritten script instead."""

    node: str
    shot_id: str | None


class NodeView(BaseModel):
    name: str
    status: NodeStatus
    duration_s: float = 0.0
    cost_usd: float = 0.0
    runs: int = 0
    error: str | None = None


_SHOT = re.compile(r"s\d{1,2}")  # shot id (schema.Shot)


def _by_shot(paths) -> list[Path]:
    """Shot files in shot order: s2 before s10 (string sorting would give s1, s10, s2)."""

    def key(p: Path) -> tuple[int, str]:
        m = re.match(r"s(\d+)", p.name)
        return (int(m.group(1)) if m else 0, p.name)

    return sorted(paths, key=key)


class VoiceArtifact(BaseModel):
    shot_id: str
    url: str | None
    tts_text: str | None = None
    line: str | None = None
    voice_id: str | None = None
    seed: int | None = None


class FrameArtifact(BaseModel):
    """Cut frame: shot shot_id starts from the frame of source_shot at at_s (spec keyframe.md)."""

    shot_id: str
    url: str | None
    approved: bool
    source_shot: str | None = None
    at_s: float | None = None


class ClipArtifact(BaseModel):
    shot_id: str
    url: str | None
    # video: the video model clip (no audio); lipsync: the same shot with lips to our audio.
    stage: Literal["video", "lipsync"] = "video"


class Artifacts(BaseModel):
    script: dict | None
    casting: dict | None
    voices: list[VoiceArtifact]
    keyframes: list[FrameArtifact]
    clips: list[ClipArtifact]
    final: str | None


class InterruptFrame(BaseModel):
    """Cut frame to approve: shot shot_id starts from the frame of source_shot at at_s."""

    shot_id: str
    path: str
    prompt: str
    source_shot: str | None = None
    at_s: float | None = None


class InterruptView(BaseModel):
    kind: Literal["approve_script", "approve_cut_frames"]
    frames: list[InterruptFrame] = []  # approve_cut_frames
    # approve_script: the script and the exact prompts the video model will get
    script: dict | None = None
    plan: VideoPlan | None = None
    next_cost_usd: float | None = None  # what approving will cost (video + lipsync)
    error: str | None = None  # the previous decision was invalid: same pause with a reason


class RunSummary(BaseModel):
    run_id: str
    idea: str
    status: str
    cost_usd: float
    updated: float


class RunView(BaseModel):
    run_id: str
    status: str
    mode: str | None
    brief: dict | None
    nodes: list[NodeView]
    cost_usd: float
    max_run_cost_usd: float
    artifacts: Artifacts
    asr: AsrCheck | None
    voice_checks: list[VoiceCheck]
    sync: SyncCheck | None
    models: dict[str, str]  # models that made the run (models.json)
    interrupt: InterruptView | None
    error: str | None
    rejected: Rejected | None = None
    auto_approve: bool | None = None  # how the run was started: "run again" repeats it
    events: list[dict]


class Limits(BaseModel):
    min_words_per_line: int
    max_words_per_line: int
    max_words_total: int
    max_lines: int
    forbidden_chars: str
    max_run_cost_usd: float
    max_daily_cost_usd: float
    deliveries: list[str]
    # Models behind each mode, shown in the UI before starting.
    models_by_mode: dict[str, dict[str, str]]
    # Notes of the selected models (`note` in models.yaml), e.g. the fake voice has no words.
    notes_by_mode: dict[str, list[str]]


class App:
    """API process state: runners per mode and a single-worker queue."""

    def __init__(self, base: Settings) -> None:
        self.base = base
        self.runners: dict[str, Runner] = {}
        # One run at a time: parallel runs multiply spending and contend for SQLite.
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.active: dict[str, str] = {}  # run_id → queued|running
        self.errors: dict[str, str] = {}
        self.lock = threading.Lock()

    def runner(self, mode: str, auto_approve: bool = False) -> Runner:
        key = f"{mode}:{auto_approve}"
        if key not in self.runners:
            s = self.base.model_copy(
                update={"pipeline_providers": mode, "auto_approve": auto_approve}
            )
            self.runners[key] = Runner(s)
        return self.runners[key]

    def submit(self, run_id: str, fn) -> None:
        with self.lock:
            self.active[run_id] = "queued"
            # The previous attempt's error is history (trace.jsonl), not this attempt's state.
            self.errors.pop(run_id, None)

        def job():
            with self.lock:
                self.active[run_id] = "running"
            try:
                res = fn()
                if res.error:
                    self.errors[run_id] = res.error
            finally:
                with self.lock:
                    self.active.pop(run_id, None)

        self.pool.submit(job)


def _media(run_dir: Path, path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    rel = path.resolve().relative_to(run_dir.resolve()).as_posix()
    # Version in the URL: after a regeneration the browser fetches the new file, not a cached one.
    return f"/media/{run_dir.name}/{rel}?v={path.stat().st_mtime_ns}"


def _cost(events: list[dict]) -> float:
    # The last record, not the maximum: refunds lower the cost.
    return events[-1].get("cum_cost_usd", 0.0) if events else 0.0


def _trace(run_dir: Path) -> list[dict]:
    t = run_dir / "trace.jsonl"
    return [json.loads(line) for line in t.open()] if t.exists() else []


def node_views(events: list[dict]) -> list[NodeView]:
    views = {n: NodeView(name=n, status="pending") for n in ORDER}
    for e in events:
        v = views.get(e["node"])
        if v is None:
            continue
        match e["event"]:
            case "start":
                v.status, v.runs, v.error = "running", v.runs + 1, None
            case "end":
                v.status, v.duration_s = "done", round(v.duration_s + e.get("duration_s", 0), 2)
            case "error":
                v.status, v.error = "error", e.get("error")
            case "waiting":
                v.status = "waiting"
            case "charge":
                v.cost_usd = round(v.cost_usd + e.get("cost_usd", 0), 4)
    return list(views.values())


def rejected_of(events: list[dict]) -> Rejected | None:
    """The run stopped on a content refusal (the last error carries the flag); the shot is the
    one the failed paid call was for — the last event of that node naming a shot."""
    errors = [i for i, e in enumerate(events) if e["event"] == "error"]
    if not errors or not events[errors[-1]].get("content_rejected"):
        return None
    node = events[errors[-1]]["node"]
    shot = next(
        (e["shot_id"] for e in reversed(events[: errors[-1]])
         if e["node"] == node and e.get("shot_id")),
        None,
    )  # fmt: skip
    return Rejected(node=node, shot_id=shot)


def _read_json(p: Path) -> dict | None:
    return json.loads(p.read_text()) if p.exists() else None


def _voice_checks(d: Path) -> list[VoiceCheck]:
    out = []
    for p in _by_shot((d / "voice").glob("*.check.json")):
        raw = _read_json(p) or {}
        out.append(
            VoiceCheck.model_validate({k: raw[k] for k in VoiceCheck.model_fields if k in raw})
        )
    return out


def _sync(d: Path) -> SyncCheck | None:
    raw = _read_json(d / "sync_check.json")
    return SyncCheck.model_validate(raw) if raw else None


def _asr(d: Path) -> AsrCheck | None:
    raw = _read_json(d / "asr_check.json")
    try:
        return AsrCheck.model_validate(raw) if raw else None
    except ValueError:  # old/short format; the status is still visible from passed
        return None


def build_app(base: Settings | None = None) -> FastAPI:
    base = base or load_settings()
    state = App(base)
    api = FastAPI(title="holywater pipeline")
    # Background runs are owned outside so tests (and server shutdown) can wait for them: a run
    # outliving the process gets killed mid native code.
    api.state.runs = state
    runs_dir = base.runs_dir
    runs_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(run_id: str) -> Path:
        d = (runs_dir / run_id).resolve()
        if d.parent != runs_dir.resolve() or not d.is_dir():
            raise HTTPException(404, f"no run {run_id}")
        return d

    def mode_of(d: Path) -> str | None:
        meta = _read_json(d / "ui.json")
        return meta["mode"] if meta else None

    def view(run_id: str) -> RunView:
        d = run_dir(run_id)
        events = _trace(d)
        nodes = node_views(events)
        mode = mode_of(d) or "fake"
        interrupt = None
        status = "unknown"
        pstate: PipelineState | None = None
        legacy = None
        try:
            res = state.runner(mode).status(run_id)
            status, interrupt, pstate = res.status, res.interrupt, res.state
        except LegacyRun:
            legacy = "This run was made by an older pipeline version: view only, start a new run."
            asr = _read_json(d / "asr_check.json")
            status = "done" if asr and asr["passed"] else "failed_verify" if asr else "unknown"
        except KeyError:
            asr = _read_json(d / "asr_check.json")
            status = "done" if asr and asr["passed"] else "failed_verify" if asr else "unknown"
        if run_id in state.active:
            status = state.active[run_id]
        elif any(n.status == "error" for n in nodes) and status not in ("done", "waiting_approval"):
            status = "error"
        brief = _read_json(d / "brief.json")
        script = _read_json(d / "script.json")
        voices = []
        # Only shot voices: technical s1.shot.wav / s1.lip.wav are not shots (not regenerated).
        for p in _by_shot((d / "voice").glob("*.wav")):
            if not _SHOT.fullmatch(p.stem):
                continue
            req = _read_json(p.with_suffix(".request.json")) or {}
            voices.append({"shot_id": p.stem, "url": _media(d, p), **req})
        frames = {k.shot_id: k for k in pstate.keyframes} if pstate else {}
        keyframes = [
            {
                "shot_id": p.stem,
                "url": _media(d, p),
                "approved": frames[p.stem].approved if p.stem in frames else status == "done",
                "source_shot": frames[p.stem].source_shot if p.stem in frames else None,
                "at_s": frames[p.stem].at_s if p.stem in frames else None,
            }
            for p in _by_shot((d / "keyframes").glob("*.png"))
        ]
        clips = [
            {"shot_id": sid, "url": _media(d, p), "stage": "lipsync" if rest else "video"}
            for p in _by_shot((d / "clips").glob("*.mp4"))
            for sid, _, rest in [p.stem.partition(".")]
            if _SHOT.fullmatch(sid) and rest in ("", "lipsync")
        ]
        final = d / "final.mp4"
        cost = _cost(events)
        return RunView(
            run_id=run_id,
            status=status,
            mode=mode,
            brief=brief,
            nodes=nodes,
            cost_usd=cost,
            max_run_cost_usd=base.max_run_cost_usd,
            artifacts={
                "script": script,
                "casting": _read_json(d / "casting.json"),
                "voices": voices,
                "keyframes": keyframes,
                "clips": clips,
                "final": _media(d, final),
            },
            asr=_asr(d),
            voice_checks=_voice_checks(d),
            models=_read_json(d / "models.json") or {},
            sync=_sync(d),
            interrupt=interrupt,
            error=state.errors.get(run_id)
            or legacy
            or next((n.error for n in nodes if n.status == "error"), None),
            rejected=rejected_of(events) if status == "error" else None,
            auto_approve=(_read_json(d / "ui.json") or {}).get("auto_approve"),
            events=events[-50:],
        )

    @api.get("/api/limits")
    def limits() -> Limits:
        by_mode = {
            m: base.model_copy(update={"pipeline_providers": m}).selected()
            for m in ("fake", "real")
        }
        return Limits(
            min_words_per_line=MIN_WORDS_PER_SHOT,
            max_words_per_line=MAX_WORDS_PER_SHOT,
            max_words_total=MAX_WORDS_TOTAL,
            max_lines=MAX_SHOTS,
            forbidden_chars="[]()*<>{}",
            max_run_cost_usd=base.max_run_cost_usd,
            max_daily_cost_usd=base.max_daily_cost_usd,
            deliveries=[d.value for d in Delivery],
            models_by_mode=by_mode,
            notes_by_mode={
                m: [n for cap, name in sel.items() if (n := registry.get(cap, name).note)]
                for m, sel in by_mode.items()
            },
        )

    @api.post("/api/preflight")
    def check(req: StartRequest) -> dict:
        brief = Brief(idea=req.idea, lines=req.lines)  # 422 with fields from FastAPI/pydantic
        r = preflight.check(
            state.runner(req.mode).settings, state.runner(req.mode).providers, brief
        )
        return {"ok": r.ok, **r.__dict__}

    @api.post("/api/runs", status_code=202)
    def start(req: StartRequest) -> dict:
        brief = Brief(idea=req.idea, lines=req.lines)
        runner = state.runner(req.mode, req.auto_approve)
        report = preflight.check(runner.settings, runner.providers, brief)
        if not report.ok:
            raise HTTPException(409, {"problems": report.problems})
        run_id = new_run_id(runs_dir)
        (runs_dir / run_id).mkdir(parents=True)
        meta = {"mode": req.mode, "auto_approve": req.auto_approve}
        write_atomic(runs_dir / run_id / "ui.json", json.dumps(meta))
        # brief.json right away, so the run shows in history while queued
        write_atomic(runs_dir / run_id / "brief.json", brief.model_dump_json(indent=2))
        if req.fresh:
            runmemo.set_fresh(runs_dir / run_id)
        state.submit(run_id, lambda: runner.start(brief, run_id))
        return {"run_id": run_id}

    @api.get("/api/runs")
    def runs() -> list[RunSummary]:
        out = []
        for d in runs_dir.iterdir():
            b = _read_json(d / "brief.json") if d.is_dir() else None
            if not b:
                continue
            events = _trace(d)
            asr = _read_json(d / "asr_check.json")
            status = state.active.get(d.name) or (
                "done" if asr and asr["passed"] else "failed_verify" if asr else "incomplete"
            )
            out.append(
                RunSummary(
                    run_id=d.name,
                    idea=b["idea"],
                    status=status,
                    cost_usd=_cost(events),
                    updated=(d / "trace.jsonl").stat().st_mtime if events else d.stat().st_mtime,
                )
            )
        return sorted(out, key=lambda r: r.updated, reverse=True)

    @api.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> RunView:
        return view(run_id)

    @api.post("/api/runs/{run_id}/approve", status_code=202)
    def approve(run_id: str, req: ApproveRequest) -> dict:
        d = run_dir(run_id)
        runner = state.runner(mode_of(d) or "fake")
        # The decision is for the current pause only: LangGraph would remember a bad one and
        # replay it on every resume (the run would stay in error until a valid decision).
        res = runner.status(run_id)
        if res.status != "waiting_approval" or not res.interrupt:
            raise HTTPException(409, f"run {run_id} is not waiting for approval")
        i = res.interrupt
        expected = (
            {"script"} if i["kind"] == "approve_script" else {f["shot_id"] for f in i["frames"]}
        )
        if set(req.decisions) != expected:
            raise HTTPException(422, f"this pause needs decisions for {sorted(expected)}")
        state.submit(run_id, lambda: runner.resume(run_id, dict(req.decisions)))
        return {"run_id": run_id}

    @api.post("/api/runs/{run_id}/regenerate", status_code=202)
    def regenerate(run_id: str, req: RegenerateRequest) -> dict:
        """New take of a shot artifact: shifts the key and seed, so it is really new; the rest
        comes from the cache. Approval is bound to the frame hash: same frames are not asked."""
        d = run_dir(run_id)
        brief = Brief.model_validate(_read_json(d / "brief.json"))
        if req.shot_id not in {f"s{i + 1}" for i in range(len(brief.lines))}:
            raise HTTPException(404, f"no shot {req.shot_id}")
        runmemo.bump_take(d, req.node, req.shot_id)
        runner = state.runner(mode_of(d) or "fake")
        state.submit(run_id, lambda: runner.start(brief, run_id))
        return {"run_id": run_id}

    @api.post("/api/runs/{run_id}/rewrite", status_code=202)
    def rewrite(run_id: str) -> dict:
        """New script take (same lines, new staging and video prompts) and the run again: the
        way out when a model refused a prompt. Stops for approval only if the run was started
        with review: a run without stops stays without stops."""
        d = run_dir(run_id)
        brief = Brief.model_validate(_read_json(d / "brief.json"))
        runmemo.bump_take(d, "script", "script")
        auto = bool((_read_json(d / "ui.json") or {}).get("auto_approve", False))
        runner = state.runner(mode_of(d) or "fake", auto)
        state.submit(run_id, lambda: runner.start(brief, run_id))
        return {"run_id": run_id}

    @api.post("/api/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str) -> dict:
        """Resume from where it stopped (error, a fal job still running). Nothing paid is
        repeated: artifacts come from the cache, unfinished fal jobs are picked up from disk."""
        d = run_dir(run_id)
        runner = state.runner(mode_of(d) or "fake")
        state.submit(run_id, lambda: runner.resume(run_id))
        return {"run_id": run_id}

    @api.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> StreamingResponse:
        trace = run_dir(run_id) / "trace.jsonl"

        async def stream():
            sent = 0
            idle = 0
            while idle < 600:  # ~5 min without events -> close; the browser reconnects
                lines = trace.read_text().splitlines() if trace.exists() else []
                for line in lines[sent:]:
                    yield f"data: {line}\n\n"
                idle = 0 if len(lines) > sent else idle + 1
                sent = len(lines)
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    api.mount("/media", StaticFiles(directory=runs_dir), name="media")
    if UI_DIST.exists():
        api.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @api.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # Unknown API/media paths are a 404, not index.html with 200.
            if path.startswith(("api/", "media/")):
                raise HTTPException(404)
            return FileResponse(UI_DIST / "index.html")

    return api
