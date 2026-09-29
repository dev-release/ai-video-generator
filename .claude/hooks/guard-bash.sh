#!/usr/bin/env bash
# PreToolUse(Bash): блокує команди, які ламають правила з CLAUDE.md,
# навіть якщо вони пройшли allowlist. Exit 2 = блок, stderr іде агенту.
set -euo pipefail

cmd=$(jq -r '.tool_input.command // ""')

block() { echo "guard-bash: $1" >&2; exit 2; }

# коміт з пропуском git-хуків = обхід воріт якості
[[ "$cmd" =~ git[[:space:]]+commit.*(--no-verify|[[:space:]]-n([[:space:]]|$)) ]] \
  && block "коміт з --no-verify заборонено: ворота якості не обходяться. Полагодь причину."

# підвищення стелі вартості з консолі
[[ "$cmd" =~ MAX_RUN_COST_USD=([0-9.]+) ]] && awk "BEGIN{exit !(${BASH_REMATCH[1]} > 5)}" \
  && block "MAX_RUN_COST_USD > 5 заборонено агенту. Підвищення стелі — рішення людини."

exit 0
