# Eval 8e1a09f-dirty-pv6 · fake · 20261002-104345

Models: script=fake, tts=kokoro, video=fake, lipsync=fake, asr=faster-whisper, face=fake

| metric | value | previous |
|---|---|---|
| cases | 29 | 29 |
| pass_rate | 0.931 | 0.931 |
| first_try_rate | 0.931 | 0.931 |
| error_rate | 0.0 | 0.0 |
| cost_total_usd | 0.0 | 0.0 |
| latency_p50_s | 6.51 | 6.53 |
| latency_p95_s | 15.77 | 16.43 |
| duration_ok_rate | 0.931 | 0.931 |
| max_silence_s | 1.29 | 1.29 |
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
| 01-letter | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 12.4 |  |
| 02-proposal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.3 |  |
| 03-twins | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.77 |  |
| 04-ceo | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.24 |  |
| 05-countdown | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.32 |  |
| 06-inheritance | failed_voice | — | — | 3 | — | 0.0 | 7.56 |  |
| 07-betrayal | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 8.83 |  |
| 08-ghost | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 4.58 |  |
| 09-percent | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.93 |  |
| 10-ordinal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.68 |  |
| 11-ampersand | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.28 |  |
| 12-dash | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.12 |  |
| 13-long | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 15.77 |  |
| 14-whisper | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.51 |  |
| 15-names | failed_voice | — | — | 3 | — | 0.0 | 6.26 |  |
| 16-time | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.35 |  |
| 17-decimal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.66 |  |
| 18-exclaim | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.93 |  |
| 19-year | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.66 |  |
| 20-hyphen | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.67 |  |
| 21-short-lines | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 8.17 |  |
| 22-ellipsis-pause | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.69 |  |
| 23-creature-gender | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.65 |  |
| 24-walk-and-talk | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.66 |  |
| 25-tells-someone | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 10.46 |  |
| 26-looks-away | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.72 |  |
| 27-ten-shots | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok s4:ok s5:ok s6:ok s7:ok s8:ok s9:ok s10:ok | 0.0 | 35.2 |  |
| 28-couple-dialogue | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 9.82 |  |
| 29-two-sisters | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok | 0.0 | 11.26 |  |
