#!/usr/bin/env bash
# SessionStart(startup|resume|clear|compact): stdout goes into the agent's context, so a new (or
# compacted) session starts from the current state. Blocks nothing: an error means less context.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

# Session start mark for the Stop hook (verify-before-stop.sh): code changes count from it.
# resume/compact is the same session: the mark stays, or unchecked changes would "disappear".
[ -t 0 ] || input=$(cat)
[[ "${input:-}" =~ \"source\"[[:space:]]*:[[:space:]]*\"(startup|clear)\" ]] && touch .claude/.session-start

section() {  # prints the PROGRESS.md section from "## $1" to the next "## "
  awk -v h="## $1" '$0 ~ "^"h {on=1; next} on && /^## / {exit} on && NF' PROGRESS.md 2>/dev/null
}

echo "## holywater-test state at session start (session-context hook)"
echo
echo "Sources of truth: CONTEXT.md (task), PROGRESS.md (state), .claude/specs/ (node contracts),"
echo "AI_LOG.md (AI mistakes). Project skills: .claude/skills/ — see the skills table in CLAUDE.md."
echo

echo "### In progress / blocker"
section "В роботі"
echo
echo "### Next (first steps)"
section "Далі" | head -4
echo

# a stub = a file with only a one-line docstring
stubs=$(for f in $(find src/pipeline -name '*.py' ! -name '__init__.py' | sort); do
  [ "$(wc -l <"$f")" -le 2 ] && printf '%s ' "${f#src/pipeline/}"
done)
[ -n "$stubs" ] && echo "### Stubs (not implemented): $stubs" && echo

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
echo "### Git: branch $branch, uncommitted paths: $dirty (commit/push only on the owner's explicit command)"

last=$(grep -E '^## [0-9]{4}-' AI_LOG.md 2>/dev/null | tail -1 | sed 's/^## //')
echo "### AI_LOG: last entry $last"
exit 0
