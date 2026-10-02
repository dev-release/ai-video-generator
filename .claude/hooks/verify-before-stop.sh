#!/usr/bin/env bash
# Stop: the agent cannot finish a reply if code changed after the last green `make check`.
# Blocks once: a repeated stop (stop_hook_active) passes — the agent has either checked or is
# explaining what is red. Runs nothing itself. Marks (not in git): .claude/.session-start from
# SessionStart, .claude/.check-ok from make check.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
input=$(cat)
[[ "$input" =~ \"stop_hook_active\"[[:space:]]*:[[:space:]]*true ]] && exit 0

start=.claude/.session-start
ok=.claude/.check-ok
# The session started before the hook existed: count changes from now.
[ -f "$start" ] || { touch "$start"; exit 0; }
ref=$start
[ -f "$ok" ] && [ "$ok" -nt "$start" ] && ref=$ok

# Everything make check covers: code, tests, UI, evals, hooks and agent docs.
changed=$(find src tests evals ui/src ui/index.html ui/package.json Makefile pyproject.toml uv.lock \
  CLAUDE.md README.md .claude/hooks .claude/skills .claude/specs .claude/settings.json \
  -type f -newer "$ref" ! -path '*/__pycache__/*' ! -name '*.pyc' ! -path 'evals/reports/*' \
  2>/dev/null | head -3 | tr '\n' ' ')
[ -z "$changed" ] && exit 0

printf '{"decision":"block","reason":"Changed after the last green make check: %s. Run make check (wrap-up skill) and report the result to the owner. If it fails, fix the cause or say plainly what is red."}\n' "${changed% }"
exit 0
