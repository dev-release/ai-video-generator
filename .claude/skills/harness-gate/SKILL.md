---
name: harness-gate
description: Додати або змінити ворота harness — хуки в .claude/hooks (guard-bash, verify-before-stop, session-context, format, pre-commit), permissions у .claude/settings.json, правила в CLAUDE.md, тести воріт і звірку документів. Використовувати, коли треба щось заборонити агенту автоматично або коли помилка AI має закритися механізмом, а не текстом.
---

# harness-gate — ворота, які реально блокують

Правило в `CLAUDE.md` агент може проігнорувати; хук і тест — ні. Помилка повторилась або коштує
дорого (ключі, гроші, git, «сказав готово — а не працює») → механізм.

## Де що

| Механізм | Файл | Що робить |
|---|---|---|
| PreToolUse(Bash), exit 2 = блок | `.claude/hooks/guard-bash.sh` | `--no-verify`, запис у `.env`, `MAX_RUN_COST_USD` > 5 |
| Stop, `decision: block` | `.claude/hooks/verify-before-stop.sh` | не дає завершити відповідь, якщо код змінено після останнього зеленого `make check` (раз; повторна зупинка проходить) |
| SessionStart | `.claude/hooks/session-context.sh` | стан проєкту в контекст; мітка старту сесії для хука Stop |
| PostToolUse(Write\|Edit) | `.claude/hooks/format-python.sh` | автоформат .py, нічого не блокує |
| git pre-commit (`make hooks`) | `.claude/hooks/pre-commit` | секрети → ruff → pytest → OpenAPI↔UI → tsc/oxlint |
| permissions allow/deny | `.claude/settings.json` | `.env`, `git push`, `reset --hard` |
| звірка документів | `tests/test_docs.py` | імена з CLAUDE.md, скілів, spec, README існують у коді |
| локальне (не в git) | `.claude/settings.local.json` | режим дозволів, дод. теки |

## Обов'язково: негативний тест

Ворота без перевірки, що вони **падають**, — декорація (AI_LOG 2026-09-29: сканер секретів
ніколи не спрацьовував через `grep -q` + `pipefail`). Нові ворота → тест у
`tests/test_harness.py`: заборонене блокується, дозволене проходить, зламане оточення (нема
`jq`) → блок, а не тихий пропуск; хук зареєстрований у `settings.json`.

## Пастки bash

- `grep -q` у пайпі під `pipefail` → SIGPIPE ламає результат. Використовуй `grep … >/dev/null`.
- Патерни секретів збігаються з текстом самого хука → виключай хук зі сканування.
- JSON зі stdin — `jq`, без нього — `python3`; нема обох → блок з поясненням (у рецензента може
  не бути `jq`).
- Шлях проєкту — `$CLAUDE_PROJECT_DIR`; хук працює на macOS і Linux (без GNU-only прапорів).
- Хук, щойно доданий у `settings.json`, може підхопитись лише в новій сесії — перевіряй його
  прямим запуском з JSON на stdin (так і роблять тести).
- Живий `guard-bash.sh` дивиться на весь текст команди: ручна перевірка з забороненим рядком
  (`echo '…--no-verify…' | bash guard-bash.sh`) сама буде заблокована. Клади JSON у файл і
  подавай через `<`.
