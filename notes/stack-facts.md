# Факти для Частини 1 (сировина, не текст)

> Збирає агент (скіл `stack-facts`): лише факти, цифри, джерела. Висновки й порівняння пише власник.
> «агрегатор» — неофіційне джерело, звірити перед використанням.

## Голос (TTS)

| провайдер/модель | факт | цифра | джерело | дата звірки |
|---|---|---|---|---|
| ElevenLabs v3 (пряме API) | ціна | $0.08 / 1k символів | https://elevenlabs.io/pricing/api | 2026-09-29 |
| ElevenLabs v4 | ціна (знижка 72% до 12 жовтня) | $0.022 / 1k символів | https://elevenlabs.io/pricing/api | 2026-09-29 |
| ElevenLabs v4 Turbo | ціна (знижка 72% до 12 жовтня) | $0.011 / 1k символів | https://elevenlabs.io/pricing/api | 2026-09-29 |
| ElevenLabs Flash/Turbo | ціна | $0.04 / 1k символів | https://elevenlabs.io/pricing/api | 2026-09-29 |
| ElevenLabs Free | включено символів v3 / міс | 10 000 | https://elevenlabs.io/pricing/api | 2026-09-29 |
| ElevenLabs v4 | дата релізу | 2026-09-28 | https://www.unite.ai/elevenlabs-launches-eleven-v4-with-low-latency-turbo-variant/ (новини) | 2026-09-29 |
| ElevenLabs v4 | model_id | `eleven_v4`, `eleven_v4_turbo` | https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4 | 2026-09-29 |
| ElevenLabs v3/v4 | керування емоцією | audio tags **всередині тексту** (`[whispering]`); SSML `<break>` у v4 вимкнено | https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4 | 2026-09-29 |
| ElevenLabs API | `seed` | «best effort… Determinism is not guaranteed», 0…4294967295 | https://elevenlabs.io/docs/api-reference/text-to-speech/convert | 2026-09-29 |
| ElevenLabs API | `voice_settings` | stability, similarity_boost, style, speaker boost, speed — окремого поля для емоції нема | https://elevenlabs.io/docs/api-reference/text-to-speech/convert | 2026-09-29 |
| ElevenLabs API | `apply_text_normalization` | `auto` / `on` / `off` | https://elevenlabs.io/docs/api-reference/text-to-speech/convert | 2026-09-29 |
| ElevenLabs v3 через fal | ціна | $0.10 / 1k символів, без підписки | https://fal.ai/models/fal-ai/elevenlabs/tts/eleven-v3 | 2026-09-29 |
| ElevenLabs v3 через fal | параметри | text (з тегами), voice, stability, similarity_boost, speed, language_code, apply_text_normalization, seed, timestamps | https://fal.ai/models/fal-ai/elevenlabs/tts/eleven-v3 | 2026-09-29 |
| ElevenLabs через fal | інші ендпоінти | multilingual-v2, turbo-v2.5, text-to-dialogue v3; v4 на сторінці нема | https://fal.ai/models/fal-ai/elevenlabs/tts/eleven-v3 | 2026-09-29 |
| Kokoro через fal | ціна | $0.02 / 1k символів; 19 голосів; speed 0.1–5.0 | https://fal.ai/models/fal-ai/kokoro/american-english | 2026-09-29 |
| Kokoro-82M | ліцензія, розмір | Apache 2.0, 82M параметрів | https://huggingface.co/hexgrad/Kokoro-82M | 2026-09-29 |
| kokoro-onnx (pip) | локальний запуск без PyTorch | модель ~300 MB (квантована ~80 MB) | https://pypi.org/project/kokoro-onnx/ | 2026-09-29 |
| Cartesia | плани | Free 20K кредитів (~27 хв TTS), Pro $5 / 100K (~133 хв), Startup $49 / 1.25M | https://cartesia.ai/pricing | 2026-09-29 |
| Cartesia | кредитів на символ | не знайдено (сторінка доків за логіном) | — | 2026-09-29 |
| MiniMax Speech | ціна | $60–100 / 1M символів (turbo / HD) | https://platform.minimax.io/docs/guides/pricing-paygo | 2026-09-29 |
| OpenAI gpt-4o-mini-tts | керування емоцією | окремий параметр `instructions`, не в `input` | https://developers.openai.com/api/docs/guides/text-to-speech | 2026-09-29 |
| OpenAI gpt-4o-mini-tts | `seed` | не згадано в доках | https://developers.openai.com/api/docs/guides/text-to-speech | 2026-09-29 |
| OpenAI TTS | ціна | не знайдено на сторінці цін | https://developers.openai.com/api/docs/pricing | 2026-09-29 |

## Відео й губи — Kling на fal

| модель | факт | цифра | джерело | дата звірки |
|---|---|---|---|---|
| Kling 3.0 Standard i2v `fal-ai/kling-video/v3/standard/image-to-video` | ціна без звуку / зі звуком / voice control | $0.084 / $0.126 / $0.154 за с | https://fal.ai/models/fal-ai/kling-video/v3/standard/image-to-video | 2026-10-01 |
| Kling 3.0 Standard i2v | параметри | `start_image_url`, `prompt`, `duration`, `generate_audio` (вимикати явно), `end_image_url` | там само | 2026-10-01 |
| Kling LipSync `fal-ai/kling-video/lipsync/audio-to-video` | ціна | $0.014 за с вхідного відео, округлення до 5 с | https://fal.ai/models/fal-ai/kling-video/lipsync/audio-to-video | 2026-10-01 |
| Kling LipSync | ліміти / час | відео 2–10 с (≤100 MB), аудіо 2–60 с (≤5 MB); ~12 хв обробки | там само | 2026-10-01 |
| Kling AI Avatar v2 Standard `fal-ai/kling-video/ai-avatar/v2/standard` | ціна | $0.0562 за с | https://fal.ai/models/fal-ai/kling-video/ai-avatar/v2/standard | 2026-10-01 |
| Kling AI Avatar v2 Standard | вхід / вихід | `image_url` + `audio_url` (+ `prompt`) → відео; тривалість = довжина аудіо | https://fal.ai/models/fal-ai/kling-video/ai-avatar/v2/standard/api | 2026-10-01 |
| Kling AI Avatar v2 Pro | ціна | $0.115 за с | https://fal.ai/models/fal-ai/kling-video/ai-avatar/v2/pro (через пошук fal) | 2026-10-01 |
