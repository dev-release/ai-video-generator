# HOLYWATER — AI Engineer test task

## What is submitted

| Required | Where |
|---|---|
| Repository with code and README (how to run, which keys) | this repo; [README.md](README.md); keys: `ANTHROPIC_API_KEY`, `FAL_KEY` |
| Note: Parts 1–3, 5 and "what I cut and why" | this file |

## The harness: how an agent writes this code without me

The code was written by Claude Code inside a harness that is committed next to it. The idea: a rule
the agent can ignore is text; a rule that blocks is a mechanism. So every rule that matters is
backed by a test, a hook or a gate.

| Layer | What it gives |
|---|---|
| [CLAUDE.md](CLAUDE.md) | the rules: never change the user's line, never commit keys, never raise the cost cap, no silent errors or retries on 4xx, the model never decides numbers, commit only on my command; when to ask me (money, commits, product decisions) and when to just proceed |
| [.claude/specs/](.claude/specs/) | a contract per stage: input, output (pydantic), invariants, cost, errors. Code is written against the spec; if they disagree, the spec is fixed first |
| [.claude/skills/](.claude/skills/) | 11 procedures for typical tasks (change a node, add a model, touch the verbatim gate, run evals…); each ends with `wrap-up` |
| [.claude/hooks/](.claude/hooks/) | they block: `git commit --no-verify`, writing `.env`, raising the cost cap; the agent cannot finish its reply if code changed after the last green check; a new session starts with the current state from `PROGRESS.md` |
| `make check` | the only definition of "done": lint, UI build, 370 tests (every media the UI shows must play, a real Chrome test, docs vs code, the hooks really block), evals on real speech |
| fake providers | the whole pipeline offline for $0, so the agent verifies end to end without spending money |
| [AI_LOG.md](AI_LOG.md) | every case where the AI was wrong → which spec hole it showed → closed by a rule, test or hook |

**How a task reaches the end without me:** the session starts with the current state → the agent
picks the skill → spec, then a test, then code → `make check` (the Stop hook will not let it skip
this) → it updates `PROGRESS.md` and, if it was wrong somewhere, `AI_LOG.md`. It stops only for
money, commits and product decisions. Example: Kling returned a 10.04 s clip for a 10 s request
and the lipsync model rejected it; the agent fixed the spec, wrote a test reproducing it, fixed the
code, resumed the run without paying for the video again and logged the case.

Examples of holes closed by a mechanism, not a sentence: 198 green unit tests while the UI showed a
0 s video → acceptance and browser tests, and `make check` as the only "done"; skills that kept
teaching removed names → `tests/test_docs.py`; a secret scanner that never fired → negative tests
for every gate. One hole is still closed only by text: after an unrequested commit, "commit only on my
command" is a rule, while `git push` is denied in the permissions.

## Part 1. Stack choice

*(author — the task asks for this part without AI; collected facts with sources:
[notes/stack-facts.md](notes/stack-facts.md))*

## Part 2. Data flow

**Principle:** the model decides only what code cannot — characters, staging, motion, lips. Words,
durations, editing, loudness, voice casting and limits are code. Picture and sound are separate
layers: a bad clip costs one clip, a bad voice costs a few cents of TTS.

```
idea ─► script ─► voice ─► voice_check ─► [approve] ─► video ─► lipsync ─► assemble ─► verify ─► sync_check
        Claude    Kokoro   whisper,                   Kling,   lips to    ffmpeg     whisper   mouth vs
                           WER == 0                   silent   our audio             WER == 0  our audio
                                         shot 1 from text ─► cut frame ─► [approve] ─► shot k from that frame
```

| Stage | Artifact in `runs/<run_id>/` | Key fields |
|---|---|---|
| input | `brief.json` | `idea`, `lines[]` — cut from the quotes **by code**, verbatim |
| script | `script.json` | characters (gender, look, voice profile); shots (speaker, line, emotion, visual, camera). The model's schema has no `line` field |
| voice | `casting.json`, `voice/sN.wav` | a distinct voice per character, picked by code; pauses between phrases set by code |
| voice_check | `voice/sN.check.json` | heard text, WER, word diff, speech window |
| approve (optional) | `video_plan.json` | the exact prompt, start, duration and price of every shot |
| video | `clips/sN.mp4`, `keyframes/sN.png` | silent clip; the frame where the edit cuts shot k−1 starts shot k |
| lipsync, assemble | `clips/sN.lipsync.mp4`, `final.mp4` | lips to our audio; 1080×1920, −14 LUFS, only our audio |
| verify, sync_check | `asr_check.json`, `sync_check.json` | WER of the final file; lip lag and correlation |
| all | `trace.jsonl` | duration, cost and retries of every step |

**State and stitching.** A LangGraph graph; its state is one pydantic object, checkpointed to SQLite
per run. Media lives on disk. Each node is `(state) -> patch`, idempotent by the hash of its input;
paid results also go to a shared cache, so the same input is never paid twice.

**Errors.** Paid requests are retried only when the provider surely did not run them (5xx, no
connection), never after a timeout or on 4xx. A fal job is saved on submit and picked up again on
resume. Invalid script JSON → a fix loop with the validation error. Bad voice → a new take of that
voice only, before any video. Final WER > 0 → stop with a word diff, no automatic paid retry.
Spending is reserved against run and daily caps before every call. Partial regeneration: a new take
of one shot's voice, video or lipsync, the rest from cache. Fallback model: one line in `.env`.

**Human in the loop** (optional; by default one command, no stops): the script with the exact video
prompts and price before the first video; the cut frame between shots. Deliberately none between
the voice and the final file — automatic gates cover it.

## Part 3. Metrics and quality

- **Technical:** latency p50/p95 per run and per stage, cost per run and per second of video, paid
  video seconds per second of speech, failure and retry rate per stage, fal queue time.
- **Content, automated:** WER == 0 on each voice and on the final file (hard gate); voice gender
  matches the face; distinct voices per character; duration ≤ 30 s and longest silence; loudness;
  face present; lip sync lag and correlation.
- **Content, manual:** is it interesting, does the hook work in 2 s, does the acting fit — a rubric
  on a sample.
- **Eval process:** 29 cases in `evals/cases` (numbers, names, pauses, whisper, dialogue, ten shots).
  Each run writes a report with the version (git sha + prompt version), models and pacing, and
  compares it to the previous report under the same conditions; a case that passed and now fails
  is a regression. The free version with real speech runs on every change (`make check`).
- **Numbers now:** 27/29 cases pass (the 2 failures are real: a sum and a name). Real runs, WER 0:
  1 shot — $0.41, 6 min; dialogue of 2 shots — $0.82, 17 min;
  from a photo — $0.99.
- **First week:** cost trace, verbatim gates, 20+ cases with regression comparison, a manual rubric
  on 5 videos. **Later:** a VLM judge calibrated against human labels, character consistency across
  shots, lip sync thresholds calibrated on dozens of clips, A/B on production data.

## Part 4. MVP

| Condition | How |
|---|---|
| Idea of 1–3 sentences → video 8–30 s | one text, the line in quotes; up to 10 lines → up to 10 shots |
| Edited: 1–2 shots | shots chained from the cut frame and edited by ffmpeg |
| The line heard word for word | the code inserts the line; WER == 0 on the voice and on the final file |
| One command → a video file | `python -m pipeline "…"` or one start in the UI |

## Part 5. How I worked with AI
(My own answer)
First of all I created a Context file before start of creating a project. This file was generated by feeding your doc and my other rules. After finished first version of this 'memory' file I start to develop with it. I created a project with a structure and technologies which were decide on first iteration. After - set up new rules of development process. 
After creating a big plan - I run plan-mode through claude to generate deep-search list of smaller tasks and do it step-by-step
When I get a ready to test proj with general idei on fake data (local or free services) - I start to test it manualy. Firstly I cheched voice generation, after lip sync, after - merging videos from a few parts to 1. After I connected real models (Kling, Claude) keys and tried with normal videos, lip sync there, audio on generated persones, few persones on 1 video and different voices
After this testing - prepare with claude a description to finish this test task

----

*(author; source material: [AI_LOG.md](AI_LOG.md) — every case where the AI was wrong, the cause and
the spec hole it showed; [CLAUDE.md](CLAUDE.md) and [.claude/](.claude/) — the harness)*

## What I cut and why — and how I would do it properly

| Cut | Why | Properly |
|---|---|---|
| LangGraph + SQLite in one process | enough for ~5 external calls | Temporal (durable execution, replay) once runs take hours or span workers; S3 + Postgres |
| Kokoro voice, emotion not voiced | free; a paid voice needs a casting session | ElevenLabs/Cartesia with a cast voice per character, fixed seeds, emotion tags |
| WER == 0 also for names and sums | the guarantee matters more than false alarms | a pronunciation lexicon, phonetic matching for names, numbers written out for TTS |
| Lip sync check only warns | thresholds from a few clips | calibrate on 50+ labelled clips, then block |
| Consistency only via the cut frame (same speaker) | no extra image step for a short video | a reference image per character, identity check between shots |
| No automatic provider failover | switching is one config line | health checks and budget-aware routing |
| Video may be shorter than 8 s | no paid silence | `PACE_MIN_FINAL_S=8` pads to 8 s |
| Single-user local UI | a demo surface | job queue with workers, per-user budgets |
