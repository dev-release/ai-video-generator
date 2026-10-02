"""Run memory on disk: takes, human approvals, "fresh" salt. Spec: pipeline.md.

Lives in the run folder, so it survives a graph restart with a clean state (UI regenerate,
CLI `--regen`), and every decision is a visible artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import TAKE_STRIDE
from pipeline.media import write_atomic

TAKES = "takes.json"
APPROVALS = "approvals.json"


def _read(run_dir: Path | str, name: str) -> dict:
    p = Path(run_dir) / name
    return json.loads(p.read_text()) if p.exists() else {}


def take(run_dir: Path | str, node: str, shot_id: str) -> int:
    return int(_read(run_dir, TAKES).get(node, {}).get(shot_id, 0))


def bump_take(run_dir: Path | str, node: str, shot_id: str) -> int:
    """New take of an artifact: shifts the cache key and the seed, so a retake is really new."""
    data = _read(run_dir, TAKES)
    n = int(data.setdefault(node, {}).get(shot_id, 0)) + 1
    data[node][shot_id] = n
    write_atomic(Path(run_dir) / TAKES, json.dumps(data, indent=2))
    return n


def seed_offset(run_dir: Path | str, node: str, shot_id: str) -> int:
    return TAKE_STRIDE * take(run_dir, node, shot_id)


def salt(run_dir: Path | str) -> str:
    """Non-empty means a deliberately fresh run (`--fresh`): the shared cache is not used."""
    return str(_read(run_dir, TAKES).get("salt", ""))


def set_fresh(run_dir: Path | str) -> None:
    data = _read(run_dir, TAKES)
    data["salt"] = Path(run_dir).name
    write_atomic(Path(run_dir) / TAKES, json.dumps(data, indent=2))


def approved_sha(run_dir: Path | str, shot_id: str) -> str | None:
    return _read(run_dir, APPROVALS).get(shot_id, {}).get("sha")


def record_approval(run_dir: Path | str, shot_id: str, sha: str, by: str) -> None:
    """Approval is bound to the frame content: same file -> not asked again; new file -> asked."""
    data = _read(run_dir, APPROVALS)
    data[shot_id] = {"sha": sha, "by": by}
    write_atomic(Path(run_dir) / APPROVALS, json.dumps(data, indent=2))
