# holywater-test

An AI agent that turns an idea of 1–3 sentences into a short vertical video (9:16): one or more
shots edited together, where the line written in the idea is heard **word for word** and the lips
move to it.

**What it solves:** a video model with its own audio can paraphrase or cut the line. Here no model
can change the words: Claude writes the characters and shots, the code inserts the line, a TTS
speaks it, and an independent speech recognizer checks it before any video is paid for and again on
the final file.

Answers to the task: [NOTES.md](NOTES.md).

## Run

Needs [uv](https://docs.astral.sh/uv/) (it brings Python 3.12) and Node ≥ 20.

```bash
make install          # dependencies + git hooks
make install-local    # local voice (Kokoro) and whisper, ~620 MB
cp .env.example .env  # set ANTHROPIC_API_KEY and FAL_KEY
make ui               # open http://localhost:5173
```

In the UI: **+ New run** → type the idea with the line in quotes, e.g.
`A girl finds a letter and whispers: "This handwriting is mine."` → **Real · paid APIs** →
**Show price & run** → **Confirm run**. The finished video appears at the top of the run page.

**Fake · $0** mode needs no keys: the same pipeline offline, with a drawn character.

## More

**CLI** — the same pipeline in one command, the result is `runs/<run_id>/final.mp4`:

```bash
uv run --extra local python -m pipeline 'A girl finds a letter and whispers: "This handwriting is mine."'
make run-fake                                               # offline, $0
make estimate                                               # checks keys, prints the cost, $0
uv run --extra local python -m pipeline --run-id <run_id>   # resume a stopped run
```

Options: `--review` (approve the script and video prompts before paying), `--mode fake`,
`--regen video:s1` (new take of one shot), `--fresh` (ignore the cache).

**Cost and time:** about $0.4–1.0 and 5–35 minutes per video, mostly the fal queue for Kling. The
caps `MAX_RUN_COST_USD` and `MAX_DAILY_COST_USD` in `.env` stop a run before a paid call that would
exceed them.

**Models** are entries in [`src/pipeline/models.yaml`](src/pipeline/models.yaml), chosen in `.env`
(`VIDEO_MODEL`, `LIPSYNC_MODEL`, …).

**Checks:** `make test` (no network), `make eval` (29 eval cases), `make check` (everything,
including a real-browser test).

**Repository:** `src/pipeline/` — the pipeline (`nodes/` one file per stage, `providers/` model
adapters, `api.py` for the UI); `ui/` — React UI; `evals/`, `tests/`; `CLAUDE.md` and `.claude/` —
the harness for the coding agent (rules, stage specs, skills, hooks).
