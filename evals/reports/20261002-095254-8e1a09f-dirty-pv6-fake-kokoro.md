# Eval 8e1a09f-dirty-pv6 · fake · 20261002-095254

Models: script=fake, tts=kokoro, video=fake, lipsync=fake, asr=faster-whisper, face=fake

| metric | value | previous |
|---|---|---|
| cases | 29 | 29 |
| pass_rate | 0.931 | 0.931 |
| first_try_rate | 0.931 | 0.931 |
| error_rate | 0.0 | 0.0 |
| cost_total_usd | 0.0 | 0.0 |
| latency_p50_s | 7.46 | 6.27 |
| latency_p95_s | 18.08 | 15.32 |
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
| 01-letter | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 14.57 |  |
| 02-proposal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.53 |  |
| 03-twins | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.75 |  |
| 04-ceo | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.41 |  |
| 05-countdown | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.46 |  |
| 06-inheritance | failed_voice | — | — | 3 | — | 0.0 | 8.56 |  |
| 07-betrayal | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 10.2 |  |
| 08-ghost | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 5.31 |  |
| 09-percent | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.97 |  |
| 10-ordinal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.73 |  |
| 11-ampersand | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.37 |  |
| 12-dash | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.92 |  |
| 13-long | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 18.08 |  |
| 14-whisper | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.39 |  |
| 15-names | failed_voice | — | — | 3 | — | 0.0 | 7.43 |  |
| 16-time | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.28 |  |
| 17-decimal | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.54 |  |
| 18-exclaim | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.82 |  |
| 19-year | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.54 |  |
| 20-hyphen | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.23 |  |
| 21-short-lines | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 9.25 |  |
| 22-ellipsis-pause | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 6.88 |  |
| 23-creature-gender | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.76 |  |
| 24-walk-and-talk | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.86 |  |
| 25-tells-someone | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 11.88 |  |
| 26-looks-away | done | 0.0 | 1 | 0 | s1:ok | 0.0 | 7.92 |  |
| 27-ten-shots | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok s4:ok s5:ok s6:ok s7:ok s8:ok s9:ok s10:ok | 0.0 | 40.6 |  |
| 28-couple-dialogue | done | 0.0 | 1 | 0 | s1:ok s2:ok | 0.0 | 11.67 |  |
| 29-two-sisters | done | 0.0 | 1 | 0 | s1:ok s2:ok s3:ok | 0.0 | 11.53 |  |
