# Eval 8e1a09f-dirty-pv6 · fake · 20261002-101011

Models: script=fake, tts=kokoro, video=fake, lipsync=fake, asr=faster-whisper, face=fake

| metric | value | previous |
|---|---|---|
| cases | 29 | 29 |
| pass_rate | 0.931 | 0.931 |
| first_try_rate | 0.931 | 0.931 |
| error_rate | 0.0 | 0.0 |
| cost_total_usd | 0.0 | 0.0 |
| latency_p50_s | 6.41 | 7.46 |
| latency_p95_s | 15.58 | 18.08 |
| duration_ok_rate | 0.931 | 0.931 |
| max_silence_s | 1.27 | 1.27 |
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
| 01-letter | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 12.28 |  |
| 02-proposal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.11 |  |
| 03-twins | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.58 |  |
| 04-ceo | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.08 |  |
| 05-countdown | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.19 |  |
| 06-inheritance | failed_voice | — | — | 3 | — | 0.0 | 7.37 |  |
| 07-betrayal | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 8.62 |  |
| 08-ghost | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 4.53 |  |
| 09-percent | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.83 |  |
| 10-ordinal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.65 |  |
| 11-ampersand | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.35 |  |
| 12-dash | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.97 |  |
| 13-long | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 15.58 |  |
| 14-whisper | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.41 |  |
| 15-names | failed_voice | — | — | 3 | — | 0.0 | 6.23 |  |
| 16-time | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.25 |  |
| 17-decimal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.58 |  |
| 18-exclaim | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.07 |  |
| 19-year | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.62 |  |
| 20-hyphen | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.7 |  |
| 21-short-lines | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 7.87 |  |
| 22-ellipsis-pause | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.61 |  |
| 23-creature-gender | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.56 |  |
| 24-walk-and-talk | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.57 |  |
| 25-tells-someone | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 10.42 |  |
| 26-looks-away | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.87 |  |
| 27-ten-shots | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok s4:ok s5:ok s6:ok s7:ok s8:ok s9:ok s10:ok | 0.0 | 34.71 |  |
| 28-couple-dialogue | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 9.87 |  |
| 29-two-sisters | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok | 0.0 | 11.13 |  |
