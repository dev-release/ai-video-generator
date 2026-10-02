"""Check before `make ui`: ports are free, and the fake-mode voice is shown.

A busy port means the previous server still runs: the new backend would not start and the UI
would talk to the old one with old settings. So stop with an explanation instead.

    python -m pipeline.devcheck 8000 5173
"""

from __future__ import annotations

import socket
import sys

from pipeline.config import Settings
from pipeline.obs import console

UI_PORTS = (8000, 5173)


def busy(ports: list[int]) -> list[int]:
    """Ports something already listens on (IPv4 or IPv6 — Vite listens on ::1 only)."""
    out = []
    for p in ports:
        try:
            with socket.create_connection(("localhost", p), timeout=0.3):
                out.append(p)
        except OSError:
            pass
    return out


def main(argv: list[str] | None = None) -> int:
    ports = [int(a) for a in (sys.argv[1:] if argv is None else argv)] or list(UI_PORTS)
    taken = busy(ports)
    if taken:
        kill = " ".join(f"$(lsof -ti :{p})" for p in taken)
        console.print(
            f"[red]✗[/] port(s) {', '.join(map(str, taken))} busy — a previous `make ui` runs."
            " A new server would not start and the UI would talk to the old one.\n"
            f"  Stop it (Ctrl+C in its terminal) or: kill {kill}"
        )
        return 1
    voice = Settings().fake_voice
    hint = "real speech" if voice == "kokoro" else "no words; FAKE_VOICE=kokoro for speech"
    console.print(f"fake-mode voice: [bold]{voice}[/] ({hint})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
