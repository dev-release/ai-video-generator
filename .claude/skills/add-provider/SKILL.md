---
name: add-provider
description: Додати, замінити або порівняти AI-модель (Claude, fal — Kling, lipsync, whisper; локальні — Kokoro тощо) у holywater-test через реєстр models.yaml, або додати новий транспорт API. Використовувати, коли задача — інша модель відео/голосу/ASR, новий провайдер API, зміна ціни чи параметрів моделі.
---

# add-provider — модель = запис у реєстрі, не код

Джерела правди: spec `.claude/specs/providers.md` (схема запису, правила для всіх моделей),
CLAUDE.md №8 (одна оболонка), №4 і №9 (ретраї й гроші). Тут — лише процедура.

## Нова модель на наявному транспорті (fal / anthropic / local) — лише yaml

1. **Доки й ціна — з офіційної сторінки**, не з пам'яті моделі: `source` (URL) і `checked`
   (дата). Для Claude — скіл `claude-api`.
2. Запис у `src/pipeline/models.yaml` за схемою spec. Відео, що вміє звук, — `native_audio: true`
   і `audio_off` в `args` (звук моделі не використовуємо). Межі входу — `input_s` (перевіряються
   до оплати кроку, що створює цей вхід).
3. `uv run pytest -q tests/test_registry.py` — контрактні тести проганяють новий запис самі.
4. `VIDEO_MODEL=<name> make estimate`, далі живий smoke
   `uv run python -m pipeline.smoke video --model <name> --yes` — **лише з дозволу власника** і з
   оцінкою суми.
5. Порівняння моделей — скіл `run-eval` з різними `*_MODEL`.

## Новий транспорт (інший API)

Один клас-адаптер на кожну можливість транспорту, від `SpecProvider`, параметризований лише
`spec` (без `if model == …`). Як у `providers/fal.py` і `providers/claude.py`:
- `ready()` перевіряє ключ/клієнт **до** резерву вартості;
- платна відправка — `retry.submit_retry`, читання — `retry.read_retry`; ретраї SDK вимкнені;
- виняток «провайдер точно не списав» має `billed = False`, «задача ще виконується» —
  `still_running = True` (їх читають `billing.not_billed` і `Tracer.spend`); фактична ціна, якщо
  відома, — у `last_cost_usd`;
- асинхронна задача пишеться на диск одразу після відправки й підхоплюється після збою (як черга
  fal у `providers/fal.py`);
- результат → файл атомарно + sidecar з URL; відповідь → pydantic на межі;
- реєстрація в `providers._adapters()`; тести адаптера на моках транспорту: 200; 4xx — без
  ретраю, резерв повернуто; 5xx — ретрай; таймаут після відправки — без повторної оплати.

## Локальні моделі

CLAUDE.md, «Стиль» (мінімум локального): лише опційний extra `local` і живий smoke до того, як на
ній будувати (AI_LOG: mediapipe).

Кінець — скіл `wrap-up`.
