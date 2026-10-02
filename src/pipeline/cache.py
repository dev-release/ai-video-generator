"""Node idempotency: key = hash of the input; a fresh artifact on disk means no provider call."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.media import write_atomic


def input_hash(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _stamp(artifact: Path) -> Path:
    return artifact.with_name(artifact.name + ".input_hash")


def is_fresh(artifact: Path, key: str) -> bool:
    stamp = _stamp(artifact)
    return artifact.exists() and stamp.exists() and stamp.read_text() == key


def mark(artifact: Path, key: str) -> None:
    write_atomic(_stamp(artifact), key)


# --- Shared cache across runs ----------------------------------------------------------------
# The same input (key) in any run -> the same paid artifact, not paid again. Stored as
# <root>/<node>/<key>/a<suffix>, the suffix being the name after the artifact stem
# (s1.png.url -> ".png.url"), so shot s2 can be restored from the cache entry of shot s1.


def _suffix(out: Path, f: Path) -> str:
    stem = out.name.split(".")[0]
    return f.name[len(stem) :]


def reuse(root: Path, node: str, key: str, out: Path, sidecars: list[Path] = ()) -> bool:
    import shutil

    slot = root / node / key
    main = slot / ("a" + _suffix(out, out))
    if not main.exists():
        return False
    for f in [out, *sidecars]:
        src = slot / ("a" + _suffix(out, f))
        if src.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(src, "rb") as fh:
                write_atomic(f, fh.read())
    shutil.copystat(main, out)
    return True


def publish(root: Path, node: str, key: str, out: Path, sidecars: list[Path] = ()) -> None:
    slot = root / node / key
    slot.mkdir(parents=True, exist_ok=True)
    for f in [out, *sidecars]:
        if f.exists():
            write_atomic(slot / ("a" + _suffix(out, f)), f.read_bytes())
