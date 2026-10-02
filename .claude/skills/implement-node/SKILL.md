---
name: implement-node
description: Реалізувати або змінити вузол пайплайну (script, voice, voice_check, keyframe, approve, video, lipsync, assemble, verify, sync_check), граф чи CLI проти специфікації. Використовувати щоразу, коли задача торкається src/pipeline/nodes/*, graph.py, __main__.py, timeline.py, media.py або стану PipelineState.
---

# implement-node — вузол проти специфікації

Джерела правди: `.claude/specs/pipeline.md` (гроші, ретраї, кеш, HITL — спільне для всіх
вузлів) і `.claude/specs/<етап>.md`. Заборони — CLAUDE.md №1, №4, №5, №9; тут вони не
переписуються, щоб копії не розходились з оригіналом.

## Кроки

1. **Прочитай** обидві spec, код вузла й тести на нього. Нема spec → скіл `write-spec`.
2. **Звір spec з кодом** (`schema.py`, `nodes/__init__.py`, `graph.py`). Розбіжність → спершу
   spec, потім код, запис в `AI_LOG.md` (скіл `log-ai-mistake`).
3. **Знайди всіх, хто читає те, що змінюєш** (поле стану, артефакт, подію трейсу):
   `grep -rn "<ім'я>" src tests evals ui/src .claude README.md`. Їх читають `api.py`, UI, евали,
   CLI — зміна без них ламає те, що бачить людина.
4. **Спершу тест** (fake-провайдери, без мережі): по тесту на кожен інваріант spec; повтор з тим
   самим входом не кличе провайдера; збій провайдера → зрозумілий виняток, оплачене не
   повторюється.
5. **Код** — чиста функція, патч лише своїх полів, платне лише через `produce`:
   ```python
   def run(state: PipelineState, ctx: Ctx) -> dict:
       out = run_path(state.run_dir, "<artifact>")
       key = cache.input_hash(<усе, що впливає на результат>, provider.name)
       produce(ctx, node="<node>", provider=provider, key=key, out=out,
               make=lambda: provider.<call>(..., out),
               estimate_usd=pricing.cost(provider.spec, seconds=...), what="<що>")
       return {"<field>": ...}
   ```
   `produce` бере свіжий файл або спільний кеш, резервує вартість до виклику, повертає резерв,
   якщо провайдер не списав, рахує фактичну ціну. Числа — з `config.py` (пайплайн) або запису
   моделі в `models.yaml`; магічне число у вузлі = баг.
6. **Кінець** — скіл `wrap-up` (зміна вузла видна в UI — перевірити й там).

## Що вже ламалось (AI_LOG)

- `Shot.line` змінено «непомітно»: `.strip()`, тег у тексті, нормалізація не в копії (№1).
- Платний виклик повз `produce` → повтор = друга оплата (аудит бюджету 2026-09-30).
- Файл записано не через `media.atomic` → UI показав напівготове відео «0 с».
- Поле стану з numpy-типом → чекпоїнт LangGraph (msgpack) падає; типи нормалізувати на межі
  провайдера.
- Петля «ворота впали → перегенерувати платне» — лише явно людиною (№9).
