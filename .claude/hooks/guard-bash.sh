#!/usr/bin/env bash
# PreToolUse(Bash): blocks commands that break CLAUDE.md rules even if the allowlist let them
# through. Exit 2 = block; stderr goes to the agent.
set -euo pipefail

block() { echo "guard-bash: $1" >&2; exit 2; }

# JSON from stdin via jq, else python3; neither -> block (silently passing would make it decoration).
input=$(cat)
if command -v jq >/dev/null 2>&1; then
  cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
elif command -v python3 >/dev/null 2>&1; then
  cmd=$(printf '%s' "$input" | python3 -c 'import json, sys
print(json.load(sys.stdin).get("tool_input", {}).get("command", ""))')
else
  block "neither jq nor python3 found — the command cannot be checked, so it is blocked. Install jq."
fi

[[ "$cmd" =~ git[[:space:]]+commit.*(--no-verify|[[:space:]]-n([[:space:]]|$)) ]] \
  && block "commit with --no-verify is not allowed: quality gates are not bypassed. Fix the cause."

# .env may be read; only the owner writes keys. Permissions do not see writes through the shell.
[[ "$cmd" =~ (\>|tee[[:space:]]+(-a[[:space:]]+)?|sed[[:space:]]+-i[^|;&]*|cp[[:space:]]+[^|;&]*|mv[[:space:]]+[^|;&]*)[[:space:]]*([^[:space:]|;&]*/)?\.env([[:space:]]|$|[\;\&\|]) ]] \
  && block "writing .env is not allowed for the agent: the owner sets the keys. Reading is fine."

[[ "$cmd" =~ MAX_RUN_COST_USD=([0-9.]+) ]] && awk "BEGIN{exit !(${BASH_REMATCH[1]} > 5)}" \
  && block "MAX_RUN_COST_USD > 5 is not allowed for the agent. Raising the cap is a human decision."

exit 0
