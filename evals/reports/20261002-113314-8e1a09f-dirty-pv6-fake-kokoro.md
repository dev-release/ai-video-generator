# Eval 8e1a09f-dirty-pv6 · fake · 20261002-113314

Models: script=fake, tts=kokoro, video=fake, lipsync=fake, asr=faster-whisper, face=fake

| metric | value | previous |
|---|---|---|
| cases | 29 | 29 |
| pass_rate | 0.931 | 0.931 |
| first_try_rate | 0.931 | 0.931 |
| error_rate | 0.0 | 0.0 |
| cost_total_usd | 0.0 | 0.0 |
| latency_p50_s | 6.46 | 6.51 |
| latency_p95_s | 24.08 | 15.77 |
| duration_ok_rate | 0.931 | 0.931 |
| max_silence_s | 1.27 | 1.29 |
| paid_per_speech | 1.51 | 1.51 |
| final_per_speech | 1.26 | 1.26 |
| voice_retry_rate | 0.069 | 0.069 |
| voice_gender_ok_rate | 1.0 | 1.0 |
| voices_distinct_rate | 1.0 | 1.0 |
| multi_speaker_cases | 3 | 3 |
| sync_ok_rate | 1.0 | 1.0 |

**Regressions:** none

| case | status | WER | attempts | voice retakes | lips | $ | s | diff |
|---|---|---|---|---|---|---|---|---|
| 01-letter | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 24.08 |  |
| 02-proposal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.67 |  |
| 03-twins | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.18 |  |
| 04-ceo | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.65 |  |
| 05-countdown | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.92 |  |
| 06-inheritance | failed_voice | — | — | 3 | — | 0.0 | 7.9 |  |
| 07-betrayal | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 9.58 |  |
| 08-ghost | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 4.55 |  |
| 09-percent | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.82 |  |
| 10-ordinal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.55 |  |
| 11-ampersand | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.19 |  |
| 12-dash | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.96 |  |
| 13-long | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 15.5 |  |
| 14-whisper | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.38 |  |
| 15-names | failed_voice | — | — | 3 | — | 0.0 | 6.1 |  |
| 16-time | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.15 |  |
| 17-decimal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.46 |  |
| 18-exclaim | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.81 |  |
| 19-year | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.36 |  |
| 20-hyphen | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.45 |  |
| 21-short-lines | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 8.64 |  |
| 22-ellipsis-pause | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.82 |  |
| 23-creature-gender | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.7 |  |
| 24-walk-and-talk | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.67 |  |
| 25-tells-someone | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 10.24 |  |
| 26-looks-away | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.73 |  |
| 27-ten-shots | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok s4:ok s5:ok s6:ok s7:ok s8:ok s9:ok s10:ok | 0.0 | 34.87 |  |
| 28-couple-dialogue | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 9.69 |  |
| 29-two-sisters | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok | 0.0 | 10.82 |  |
