"""Evals: run the pipeline over evals/cases -> report + comparison with the previous version.

    python -m evals                      # fake, no models (tone + echo): gate and metric mechanics
    python -m evals --voice kokoro       # offline with real speech (make install-local)
    python -m evals --mode real --yes    # real providers, only with --yes and within the caps
    python -m evals --only 01-letter,06-inheritance

Exit code != 0 on a regression against the previous report (a case passed before, fails now).
Metrics come only from artifacts (asr_check.json, trace.jsonl, final.mp4); no model judges.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import yaml

from pipeline import preflight
from pipeline.config import ROOT, Settings
from pipeline.graph import Runner
from pipeline.media import probe, silent_runs
from pipeline.nodes import script as script_node
from pipeline.obs import console
from pipeline.schema import Brief, profile_gender

CASES = ROOT / "evals" / "cases"
REPORTS = ROOT / "evals" / "reports"


def version() -> str:
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()

    sha = git("rev-parse", "--short", "HEAD") or "nogit"
    dirty = "-dirty" if git("status", "--porcelain", "--", "src") else ""
    return f"{sha}{dirty}-p{script_node.PROMPT_VERSION}"


def load_cases(only: set[str] | None) -> list[dict]:
    cases = [yaml.safe_load(p.read_text()) for p in sorted(CASES.glob("*.yaml"))]
    return [c for c in cases if not only or c["id"] in only]


def node_latency(trace: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in trace.open():
        rec = json.loads(line)
        if rec["event"] == "end":
            out[rec["node"]] = round(out.get(rec["node"], 0) + rec["duration_s"], 2)
    return out


def run_case(runner: Runner, case: dict) -> dict:
    t0 = time.perf_counter()
    res = runner.start(Brief(idea=case["idea"], lines=case["lines"]), run_id=case["id"])
    row = {
        "id": case["id"],
        "tags": case.get("tags", []),
        "status": res.status,
        "latency_s": round(time.perf_counter() - t0, 2),
        "cost_usd": round(runner.cost(case["id"]), 4),
        "error": res.error,
    }
    s = res.state
    if s and s.asr:
        row |= {
            "passed": s.asr.passed,
            "wer": s.asr.wer,
            "verify_attempts": s.verify_attempt,
            "first_try": s.asr.passed and s.verify_attempt == 1,
            "diff": [d.model_dump() for d in s.asr.diff],
        }
    if s:
        row["voice_retries"] = sum(s.voice_retries.values())
    if s and s.script:
        row["voice_gender_ok"] = all(
            c.gender is not None and profile_gender(c.voice_profile) == c.gender
            for c in s.script.characters
        )
        speakers = {sh.character_id for sh in s.script.shots}
        row["speakers"] = len(speakers)
        row["voices_distinct"] = len({s.casting[c].voice_id for c in speakers}) == len(speakers)
    if s and s.sync:
        row["sync"] = {
            sh.shot_id: {"verdict": sh.verdict, "lag_ms": sh.best_lag_ms, "corr": sh.corr}
            for sh in s.sync.shots
        }
    if s and s.final:
        row |= {"final_s": s.final.duration_s, "lufs": s.final.lufs}
        # Dead time: silence longer than the scripted pauses wastes money and attention; we pay
        # for clip seconds, not for speech.
        speech = sum(v.duration_s for v in s.voices)
        silences = silent_runs(Path(s.final.path))
        row |= {
            "speech_s": round(speech, 2),
            "max_silence_s": round(max((b - a for a, b in silences), default=0.0), 2),
            "paid_video_s": round(sum(probe(Path(c.path)).duration_s for c in s.clips), 2),
        }
    trace = Path(runner.settings.runs_dir, case["id"], "trace.jsonl")
    if trace.exists():
        row["node_latency_s"] = node_latency(trace)
    return row


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    lat = sorted(r["latency_s"] for r in rows)
    return {
        "cases": n,
        "pass_rate": round(sum(r.get("passed", False) for r in rows) / n, 3),
        "first_try_rate": round(sum(r.get("first_try", False) for r in rows) / n, 3),
        "error_rate": round(sum(r["status"] == "error" for r in rows) / n, 3),
        "cost_total_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "latency_p50_s": median(lat),
        "latency_p95_s": lat[min(n - 1, int(0.95 * n))],
        "duration_ok_rate": round(sum(0 < r.get("final_s", 0) <= 30 for r in rows) / n, 3),
        "max_silence_s": max((r.get("max_silence_s", 0.0) for r in rows), default=0.0),
        # Paid video seconds per second of speech: 1.0 is ideal; Kling bills whole seconds from 3.
        "paid_per_speech": _ratio(rows, "paid_video_s", "speech_s"),
        "final_per_speech": _ratio(rows, "final_s", "speech_s"),
        "voice_retry_rate": round(sum(r.get("voice_retries", 0) > 0 for r in rows) / n, 3),
        "voice_gender_ok_rate": round(sum(r.get("voice_gender_ok", False) for r in rows) / n, 3),
        "voices_distinct_rate": round(sum(r.get("voices_distinct", False) for r in rows) / n, 3),
        "multi_speaker_cases": sum(r.get("speakers", 0) > 1 for r in rows),
        # Only shots with a measurable face count (n/a is neither ok nor off).
        "sync_ok_rate": _sync_rate(rows),
    }


def _ratio(rows: list[dict], num: str, den: str) -> float | None:
    a, b = sum(r.get(num, 0.0) for r in rows), sum(r.get(den, 0.0) for r in rows)
    return round(a / b, 2) if b else None


def _sync_rate(rows: list[dict]) -> float | None:
    verdicts = [v["verdict"] for r in rows for v in r.get("sync", {}).values()]
    judged = [v for v in verdicts if v != "n/a"]
    return round(sum(v == "ok" for v in judged) / len(judged), 3) if judged else None


def previous_report(mode: str, voice: str) -> dict | None:
    # Compare like with like: tone vs Kokoro or fake vs real is not a regression.
    reports = sorted(REPORTS.glob(f"*-{mode}-{voice}.json"))
    return json.loads(reports[-1].read_text()) if reports else None


def regressions(prev: dict | None, rows: list[dict]) -> list[str]:
    if not prev:
        return []
    was = {r["id"]: r.get("passed", False) for r in prev["rows"]}
    return [r["id"] for r in rows if was.get(r["id"]) and not r.get("passed", False)]


def markdown(report: dict, prev: dict | None, regressed: list[str]) -> str:
    s = report["summary"]
    lines = [
        f"# Eval {report['version']} · {report['mode']} · {report['started']}",
        "",
        "Models: " + ", ".join(f"{k}={v}" for k, v in report["models"].items()),
        "",
        "| metric | value | previous |",
        "|---|---|---|",
    ]
    ps = (prev or {}).get("summary", {})
    for k, v in s.items():
        lines.append(f"| {k} | {v} | {ps.get(k, '—')} |")
    lines += ["", f"**Regressions:** {', '.join(regressed) or 'none'}", ""]
    lines += [
        "| case | status | WER | attempts | voice retakes | lips | $ | s | diff |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        diff = "; ".join(f"{d['expected']}→{d['heard']}" for d in r.get("diff", [])) or ""
        lips = " ".join(f"{k}:{v['verdict']}" for k, v in r.get("sync", {}).items()) or "—"
        lines.append(
            f"| {r['id']} | {r['status']} | {r.get('wer', '—')} | {r.get('verify_attempts', '—')} "
            f"| {r.get('voice_retries', '—')} | {lips} | {r['cost_usd']} | {r['latency_s']} "
            f"| {diff or (r['error'] or '')[:80]} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evals")
    p.add_argument("--mode", choices=["fake", "real"], default="fake")
    p.add_argument("--voice", choices=["kokoro", "tone"], default="tone")
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--yes", action="store_true", help="confirm spending in real mode")
    a = p.parse_args(argv)

    started = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    ver = version()
    runs_dir = ROOT / "runs" / "_evals" / f"{started}-{a.mode}"
    # Shared cache: a repeated eval pays only for stages whose input changed.
    settings = Settings(
        pipeline_providers=a.mode,
        fake_voice=a.voice,
        auto_approve=True,
        runs_dir=runs_dir,
        cache_dir=ROOT / "runs" / "_cache",
    )
    cases = load_cases(set(a.only.split(",")) if a.only else None)
    runner = Runner(settings)

    if a.mode == "real":
        total = worst = 0.0
        for c in cases:
            r = preflight.check(settings, runner.providers, Brief(idea=c["idea"], lines=c["lines"]))
            if not r.ok:
                for e in r.problems:
                    console.print(f"[red]✗[/] {c['id']}: {e}")
                return 2
            total, worst = total + r.estimate_usd, worst + r.worst_usd
        console.print(f"{len(cases)} cases: ~${total:.2f} (up to ${worst:.2f})")
        left = r.daily_left_usd
        if total > left:
            # Do not start what the daily cap would cut off halfway.
            console.print(
                f"[red]✗[/] estimate ${total:.2f} > ${left:.2f} left today (MAX_DAILY_COST_USD). "
                "Pick fewer cases: --only 01-letter,04-ceo"
            )
            return 2
        if not a.yes:
            console.print("[yellow]a real run spends money — repeat with --yes[/]")
            return 2

    rows = []
    for c in cases:
        console.rule(c["id"])
        rows.append(run_case(runner, c))

    prev = previous_report(a.mode, a.voice)
    regressed = regressions(prev, rows)
    report = {
        "version": ver,
        "mode": a.mode,
        "voice": a.voice,
        "started": started,
        # Models and pacing are part of the version, compared like code changes.
        "models": runner.providers.models(),
        "pacing": settings.pacing().model_dump(),
        "summary": summarize(rows),
        "rows": rows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    base = REPORTS / f"{started}-{ver}-{a.mode}-{a.voice}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    base.with_suffix(".md").write_text(markdown(report, prev, regressed))
    console.print(f"\nreport: {base.with_suffix('.md').relative_to(ROOT)}")
    console.print(report["summary"])
    if regressed:
        console.print(f"[red]regressions: {', '.join(regressed)}[/]")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
