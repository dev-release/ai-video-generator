# holywater-test

Ідея в 1–3 речення → вертикальне відео 8–30 с, де репліка звучить **дослівно**, як у сценарії.

> Статус: каркас. Пайплайн ще не реалізовано.

## Запуск

```bash
make install                  # uv sync + git-хуки
cp .env.example .env          # вставити ключі (опис кожного — у файлі)
make run IDEA="A girl finds a letter from her future self"
# → runs/<run_id>/final.mp4
```

Без ключів і грошей: `make run-fake`.

## Ключі
Див. `.env.example`.

## Структура
Див. `CONTEXT.md` §7. Правила для агента — `CLAUDE.md`, специфікації етапів — `.claude/specs/`.
