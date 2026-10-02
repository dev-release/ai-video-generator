"""Agent instructions do not lie: names in CLAUDE.md, skills, specs and README exist in the repo.

A skill or spec with a stale name is a wrong instruction: the agent looks for what does not exist
or rebuilds the old thing. Only mechanical things are checked: `pipeline.*` modules and
attributes, paths, make targets, constants and env vars. It does not replace the "one rule, one
place" principle (CLAUDE.md).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from pipeline.config import ROOT, Settings

PKG = ROOT / "src" / "pipeline"
DOCS = [
    ROOT / "CLAUDE.md",
    ROOT / "README.md",
    *sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")),
    *sorted((ROOT / ".claude" / "specs").glob("*.md")),
]
FENCE = re.compile(r"```.*?```", re.S)
TICKS = re.compile(r"`([^`\n]+)`")
MODULES = {p.stem for p in PKG.glob("*.py")} | {
    p.name for p in PKG.iterdir() if (p / "__init__.py").exists()
}
MODULE_ATTR = re.compile(r"(?<![\w.])([a-z_]+)\.([A-Za-z_]\w*)")
FILE_EXT = (".py", ".sh", ".ts", ".tsx", ".yaml", ".toml", ".md", ".json", ".onnx")
TOP_DIRS = {"src", "tests", "evals", "ui", ".claude", "notes", "nodes", "providers"}
# Local or runtime-only things: a fresh clone does not have them, and that is fine.
LOCAL = ("runs/", "~", "http", "docs/task", ".env", ".claude/settings.local.json", "ui/dist",
         "ui/node_modules", ".claude/.")  # fmt: skip
PLACEHOLDER = re.compile(r"[<>*…{}$|\s]")
CONST = re.compile(r"(?<![\w.*])([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)(?![\w*.])")
MAKE = re.compile(r"(?:^|\s)make ([a-z][\w-]*)")


def _corpus() -> str:
    files = [
        *PKG.rglob("*.py"),
        *PKG.glob("*.yaml"),
        *(ROOT / "evals").glob("*.py"),
        *(p for p in (ROOT / ".claude" / "hooks").iterdir() if p.is_file()),
        ROOT / "Makefile",
        ROOT / ".env.example",
    ]
    return "\n".join(p.read_text(errors="ignore") for p in files)


CORPUS = _corpus()
SETTINGS_ENV = {f.upper() for f in Settings.model_fields}
MAKE_TARGETS = set(re.findall(r"^([a-z][\w-]*):", (ROOT / "Makefile").read_text(), re.M))


def _module_attr(mod: str, attr: str) -> str | None:
    if mod not in MODULES or attr in {"py", "md", "yaml", "json", "sh"}:
        return None
    m = importlib.import_module(f"pipeline.{mod}")
    if hasattr(m, attr):
        return None
    try:  # a package submodule: `nodes.voice`
        importlib.import_module(f"pipeline.{mod}.{attr}")
        return None
    except ModuleNotFoundError:
        return f"pipeline.{mod} has no `{attr}`"


def _path(tok: str) -> str | None:
    t = tok.rstrip("/")
    if tok.startswith(LOCAL) or PLACEHOLDER.search(tok):
        return None
    if "/" in t and (t.split("/")[0] in TOP_DIRS or t.endswith(FILE_EXT)):
        return None if (ROOT / t).exists() or (PKG / t).exists() else f"no path `{tok}`"
    if "/" not in t and t.endswith(FILE_EXT) and not t.endswith((".md", ".json")):
        # a short file name (`schema.py`) must exist somewhere in the repo code
        roots = (PKG, ROOT / "tests", ROOT / "evals", ROOT / ".claude", ROOT / "ui" / "src")
        if (ROOT / t).exists() or any(any(r.rglob(t)) for r in roots):
            return None
        return f"no file `{tok}`"
    return None


def problems(doc: Path) -> list[str]:
    text = doc.read_text()
    found: list[str] = []
    for block in FENCE.findall(text):
        found += [e for m, a in MODULE_ATTR.findall(block) if (e := _module_attr(m, a))]
    for tok in TICKS.findall(FENCE.sub("", text)):
        if (m := re.fullmatch(r"([a-z_]+)\.([A-Za-z_]\w*)(\(.*\))?", tok)) and (
            e := _module_attr(m[1], m[2])
        ):
            found.append(e)
        if e := _path(tok):
            found.append(e)
        found += [f"no make target `make {t}`" for t in MAKE.findall(tok) if t not in MAKE_TARGETS]
        found += [
            f"`{c}` is not in the code, .env.example or Makefile"
            for c in CONST.findall(tok)
            if c not in CORPUS and c not in SETTINGS_ENV
        ]
    return found


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_names_in_agent_docs_exist(doc: Path):
    assert not problems(doc), f"{doc.relative_to(ROOT)}:\n" + "\n".join(problems(doc))


def test_checker_catches_stale_names(tmp_path):
    """The check itself must fail on stale names, otherwise it is decoration."""
    doc = tmp_path / "SKILL.md"
    doc.write_text(
        "retries: `retry.network_retry`; prices: `config.PRICES_USD`; `make ui-magic`;\n"
        "`src/pipeline/providers/google.py`; `GOOGLE_API_KEY`; alive: `nodes.produce`, "
        "`media.atomic`, `make check`, `providers/fal.py`, `MAX_RUN_COST_USD`, `runs/<id>/x`\n"
        "```python\nretry.network_retry(x)\n```\n"
    )
    found = problems(doc)
    assert len(found) == 6, found
    assert sum("network_retry" in p for p in found) == 2
