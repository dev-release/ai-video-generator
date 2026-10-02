"""One command -> one video file, no prompts by default.

python -m pipeline 'A girl finds a letter and whispers: "This handwriting is mine."'
python -m pipeline "idea" --line "exact line" [--line "second line"]

python -m pipeline '…' --review                      # stop for approval before paid video
python -m pipeline '…' --estimate                    # checks and cost estimate only, $0
python -m pipeline --run-id 2026-09-30-01            # resume (after an error / a running job)
python -m pipeline --run-id 2026-09-30-01 --regen voice:s1   # new take of one shot artifact
python -m pipeline '…' --fresh                       # ignore the shared cache of paid results
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from pipeline import preflight, runmemo
from pipeline.config import Settings
from pipeline.graph import Runner, RunResult, new_run_id
from pipeline.obs import console
from pipeline.schema import Brief


def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pipeline", description="idea + lines -> vertical video")
    p.add_argument("idea", nargs="?", help="the idea, 1–3 sentences, lines in quotes")
    p.add_argument("--line", action="append", default=[], help="exact line (up to 10 times)")
    p.add_argument("--run-id", help="resume or restart an existing run")
    p.add_argument("--mode", choices=["fake", "real"], help="override PIPELINE_PROVIDERS")
    p.add_argument(
        "--review", action="store_true",
        help="stop before paid video: approve the script with video prompts and the cut frames",
    )  # fmt: skip
    p.add_argument(
        "--regen", action="append", default=[], metavar="NODE:SHOT",
        help="new take: voice|video|lipsync:s1 (with --run-id)",
    )  # fmt: skip
    p.add_argument("--fresh", action="store_true", help="ignore the shared cache of paid results")
    p.add_argument("--estimate", action="store_true", help="preflight and cost estimate only")
    return p.parse_args(argv)


def _print_report(r: preflight.Report) -> None:
    for w in r.warnings:
        console.print(f"[yellow]![/] {w}")
    for e in r.problems:
        console.print(f"[red]✗[/] {e}")
    if r.estimate_usd or r.worst_usd:
        console.print(
            f"estimate: ~${r.estimate_usd:.2f} (up to ${r.worst_usd:.2f} with retries); "
            f"run cap ${r.max_run_usd:.2f}, left today ${r.daily_left_usd:.2f}"
        )


def _choose(prompt: str) -> str:
    # Only an explicit answer: an empty Enter used to trigger a paid regeneration.
    while (a := input(prompt).strip().lower()) not in ("a", "r", "q"):
        pass
    return a


def _ask_script(i: dict) -> dict[str, str] | None:
    """The script and the exact prompts the video model will get (spec approve_script.md)."""
    script, plan = i["script"], i["plan"]
    console.print(f"\n[bold]{script['title']}[/] · {script['style']}")
    for c in script["characters"]:
        console.print(f"  {c['id']}: {c['look']} · voice {c['voice_profile']}")
    for sp in plan["shots"]:
        start = "from text" if sp["start"] == "text" else f"from cut frame of {sp['source_shot']}"
        console.print(
            f"\n[bold]{sp['shot_id']}[/] {sp['character_id']} · {sp['delivery']} · {start} · "
            f"shot {sp['shot_s']:.1f} s, clip {sp['clip_s']:.0f} s\n  “{sp['line']}”\n"
            f"  [dim]prompt:[/] {sp['prompt']}"
        )
    if plan.get("negative_prompt"):
        console.print(f"\n[dim]negative_prompt:[/] {plan['negative_prompt']}")
    console.print(f"\napproving runs video and lipsync of all shots ({plan['model']}): "
                  f"~${i['next_cost_usd']:.2f}")  # fmt: skip
    a = _choose("  [a]pprove / [r]ewrite script / [q]uit > ")
    return None if a == "q" else {"script": "approve" if a == "a" else "regenerate"}


def _ask(result: RunResult) -> dict[str, str] | None:
    if result.interrupt["kind"] == "approve_script":
        return _ask_script(result.interrupt)
    cost = result.interrupt.get("next_cost_usd")
    if cost is not None:
        console.print(f"\napproving runs video and lipsync of the next shot: ~${cost:.2f}")
    decisions: dict[str, str] = {}
    for f in result.interrupt["frames"]:
        clip = Path(f["path"]).parents[1] / "clips" / f"{f['source_shot']}.mp4"
        console.print(
            f"\n[bold]start of {f['shot_id']}[/] = frame of {f['source_shot']} "
            f"at {f['at_s']:.2f} s\n"
            f"  frame: {f['path']}\n  video {f['source_shot']} (compare its end): {clip}\n"
            "  r — new take of the previous shot's video"
        )
        a = _choose("  [a]pprove / [r]egenerate / [q]uit > ")
        if a == "q":
            return None
        decisions[f["shot_id"]] = "approve" if a == "a" else "regenerate"
    return decisions


def main(argv: list[str] | None = None) -> int:
    a = _args(argv)
    overrides: dict = {"auto_approve": not a.review}
    if a.mode:
        overrides["pipeline_providers"] = a.mode
    settings = Settings(**overrides)

    brief = None
    if a.idea or a.line:
        try:
            brief = Brief(idea=a.idea or "", lines=a.line)
        except ValidationError as e:
            for err in e.errors():
                where = ".".join(map(str, err["loc"]))
                msg = err["msg"].removeprefix("Value error, ")
                console.print(f"[red]✗[/] {where + ': ' if where else ''}{msg}")
            return 2
    elif not a.run_id:
        console.print('[red]✗[/] give an idea with the line in quotes, or --line "…" (or --run-id)')
        return 2
    if a.regen and not a.run_id:
        console.print("[red]✗[/] --regen needs --run-id of an existing run")
        return 2

    try:
        runner = Runner(settings)
    except NotImplementedError as e:
        console.print(f"[red]✗[/] {e}")
        return 2
    report = preflight.check(settings, runner.providers, brief)
    _print_report(report)
    if not report.ok:
        return 2
    if a.estimate:
        return 0

    if a.regen:
        run_dir = settings.runs_dir / a.run_id
        brief = brief or Brief.model_validate_json((run_dir / "brief.json").read_text())
        for item in a.regen:
            node, _, shot = item.partition(":")
            if node not in ("voice", "video", "lipsync") or not shot:
                console.print(f"[red]✗[/] --regen {item}: expected voice|video|lipsync:sN")
                return 2
            runmemo.bump_take(run_dir, node, shot)
    if brief and a.fresh:
        run_id = a.run_id or new_run_id(settings.runs_dir)
        runmemo.set_fresh(settings.runs_dir / run_id)
        a.run_id = run_id
    result = runner.start(brief, a.run_id) if brief else runner.resume(a.run_id)
    while result.status == "waiting_approval":
        if not sys.stdin.isatty():
            d = settings.runs_dir / result.run_id
            what = (f"the script and video prompts: {d / 'video_plan.json'}"
                    if result.interrupt["kind"] == "approve_script"
                    else f"the cut frame between shots: {d / 'keyframes'}")  # fmt: skip
            console.print(f"[yellow]⏸[/] waiting for approval of {what}")
            console.print(f"   resume: python -m pipeline --run-id {result.run_id}")
            return 3
        decisions = _ask(result)
        if decisions is None:
            console.print(f"stopped; resume: python -m pipeline --run-id {result.run_id}")
            return 3
        result = runner.resume(result.run_id, decisions)

    state = result.state
    cost = runner.cost(result.run_id)
    if result.status == "done":
        console.print(f"\n[bold green]✓ {state.final.path}[/]  WER 0  ${cost:.2f}")
        return 0
    if result.status == "failed_verify":
        diff = "; ".join(f"'{d.expected}' → '{d.heard}'" for d in state.asr.diff)
        console.print(f"\n[red]✗ verbatim gate failed[/] (WER {state.asr.wer}): {diff}")
        console.print(f"  file: {state.final.path} (asr_check.json: passed=false)")
        return 1
    console.print(f"\n[red]✗ {result.error}[/]\n  trace: runs/{result.run_id}/trace.jsonl")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
