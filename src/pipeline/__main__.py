"""Одна команда → відеофайл: `python -m pipeline "ідея"`."""

import sys


def main() -> int:
    print("pipeline: ще не реалізовано — див. .claude/specs/", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
