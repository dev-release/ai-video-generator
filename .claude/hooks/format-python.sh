#!/usr/bin/env bash
# PostToolUse(Write|Edit): форматує змінений .py файл ruff'ом.
# Нічого не блокує — лінт-помилки ловить pre-commit.
f=$(jq -r '.tool_response.filePath // .tool_input.file_path // ""')
[[ "$f" == *.py && -f "$f" ]] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
uv run --quiet ruff format "$f" >/dev/null 2>&1 || true
uv run --quiet ruff check --fix --quiet "$f" >/dev/null 2>&1 || true
exit 0
