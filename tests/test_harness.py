"""Harness gates block when they should and pass when they should (skill harness-gate).

A gate without a negative test is decoration: a secret scanner once "worked" without ever
firing. Hooks run the way Claude Code calls them: bash, JSON on
stdin, CLAUDE_PROJECT_DIR.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from pipeline.config import ROOT

HOOKS = ROOT / ".claude" / "hooks"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="no bash: Claude Code hooks would not run")


def _hook(name: str, payload: dict, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=30,
    )


# --- guard-bash: forbidden commands are blocked (exit 2), allowed ones pass


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "echo FAL_KEY=x > .env",
        "cp .env.example .env",
        "MAX_RUN_COST_USD=10 make run",
    ],
)
def test_guard_blocks(cmd):
    r = _hook("guard-bash.sh", {"tool_input": {"command": cmd}})
    assert r.returncode == 2, (cmd, r.stderr)


@pytest.mark.parametrize(
    "cmd", ["ls", "cat .env", "git commit -m fine", "MAX_RUN_COST_USD=1 make run-fake"]
)
def test_guard_allows(cmd):
    r = _hook("guard-bash.sh", {"tool_input": {"command": cmd}})
    assert r.returncode == 0, (cmd, r.stderr)


def _only(tmp_path: Path, *tools: str) -> str:
    """PATH with only the listed tools, like a reviewer's machine without jq."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in tools:
        if (src := shutil.which(tool)) is None:
            pytest.skip(f"no {tool}")
        (bin_dir / tool).symlink_to(src)
    return str(bin_dir)


def test_guard_without_jq_falls_back_to_python(tmp_path):
    path = _only(tmp_path, "cat", "awk", "python3")
    r = _hook(
        "guard-bash.sh", {"tool_input": {"command": "git commit --no-verify -m x"}}, PATH=path
    )
    assert r.returncode == 2, r.stderr


def test_guard_without_json_tools_blocks_instead_of_passing(tmp_path):
    path = _only(tmp_path, "cat", "awk")
    r = _hook("guard-bash.sh", {"tool_input": {"command": "ls"}}, PATH=path)
    assert r.returncode == 2 and "jq" in r.stderr, r.stderr


# --- verify-before-stop: code changed after a green make check -> the agent cannot finish


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".claude").mkdir(parents=True)
    (p / "src").mkdir()
    return p


def _age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


def _session(p: Path, started_ago: float = 60) -> None:
    mark = p / ".claude" / ".session-start"
    mark.touch()
    _age(mark, started_ago)


def _stop(p: Path, active: bool = False) -> subprocess.CompletedProcess:
    return _hook("verify-before-stop.sh", {"stop_hook_active": active}, CLAUDE_PROJECT_DIR=str(p))


def test_stop_blocks_when_code_changed_without_check(tmp_path):
    p = _project(tmp_path)
    _session(p)
    (p / "src" / "a.py").write_text("x = 1\n")
    out = json.loads(_stop(p).stdout)
    assert out["decision"] == "block"
    assert "src/a.py" in out["reason"] and "make check" in out["reason"]


def test_stop_passes_after_green_check(tmp_path):
    p = _project(tmp_path)
    _session(p)
    (p / "src" / "a.py").write_text("x = 1\n")
    _age(p / "src" / "a.py", 30)
    (p / ".claude" / ".check-ok").touch()
    assert _stop(p).stdout == ""


def test_stop_blocks_only_once(tmp_path):
    p = _project(tmp_path)
    _session(p)
    (p / "src" / "a.py").write_text("x = 1\n")
    assert _stop(p, active=True).stdout == ""


def test_stop_ignores_bytecode_and_changes_before_session(tmp_path):
    p = _project(tmp_path)
    (p / "src" / "a.py").write_text("x = 1\n")
    _age(p / "src" / "a.py", 120)  # changed before the session started: not the agent's change
    _session(p)
    (p / "src" / "__pycache__").mkdir()
    (p / "src" / "__pycache__" / "a.cpython-314.pyc").write_bytes(b"")
    assert _stop(p).stdout == ""


def test_stop_without_session_mark_starts_counting(tmp_path):
    p = _project(tmp_path)
    (p / "src" / "a.py").write_text("x = 1\n")
    assert _stop(p).stdout == ""
    assert (p / ".claude" / ".session-start").exists()


def test_session_start_marks_only_new_sessions(tmp_path):
    p = _project(tmp_path)
    mark = p / ".claude" / ".session-start"
    _hook("session-context.sh", {"source": "compact"}, CLAUDE_PROJECT_DIR=str(p))
    assert not mark.exists()  # same session: unchecked changes must not "disappear"
    _hook("session-context.sh", {"source": "startup"}, CLAUDE_PROJECT_DIR=str(p))
    assert mark.exists()


# --- gates are wired: a hook missing from settings.json never fires


def test_hooks_are_wired_and_check_stamps_only_on_success():
    groups = json.loads((ROOT / ".claude" / "settings.json").read_text())["hooks"]
    wired = {
        ev: " ".join(h["command"] for g in gs for h in g["hooks"]) for ev, gs in groups.items()
    }
    assert "guard-bash.sh" in wired["PreToolUse"]
    assert "verify-before-stop.sh" in wired["Stop"]
    assert "session-context.sh" in wired["SessionStart"]
    block = re.search(r"^check:.*?(?=^\S)", (ROOT / "Makefile").read_text(), re.M | re.S)[0]
    last = [line for line in block.splitlines() if line.strip()][-1]
    assert "touch .claude/.check-ok" in last  # the last step: the mark only after all pass


def test_make_ui_refuses_busy_ports_instead_of_silently_using_an_old_server():
    """Regression: an old `make ui` held :8000, the new backend silently did not start and the
    UI talked to the old server with the old voice. The gate runs before starting servers."""
    import socket

    from pipeline import devcheck

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        assert devcheck.busy([port]) == [port]
        assert devcheck.main([str(port)]) == 1
    finally:
        srv.close()
    assert devcheck.busy([port]) == []
    ui = re.search(r"^ui:\n((?:\t.*\n)+)", (ROOT / "Makefile").read_text(), re.M)
    assert ui and ui.group(1).splitlines()[0].strip().endswith("pipeline.devcheck 8000 5173")


def test_onnxruntime_telemetry_is_off_before_it_can_start():
    """`make check` exited 134 after green tests: onnxruntime telemetry (Kokoro) uploaded at
    process exit and hit a destroyed mutex. Only an env var set before onnxruntime initializes
    turns it off — so it lives in the package root, and onnxruntime is imported lazily."""
    import sys

    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    code = "import os, pipeline; print(os.environ.get('ORT_DISABLE_TELEMETRY'))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "1", out.stderr
    eager = re.compile(r"^(import|from) (onnxruntime|kokoro_onnx|faster_whisper)\b", re.M)
    assert [p.name for p in (ROOT / "src").rglob("*.py") if eager.search(p.read_text())] == []
