.PHONY: check install install-local run run-fake estimate eval eval-speech test lint fmt hooks ui ui-build ui-types ui-check

IDEA ?= A girl finds a letter from her future self
LINE ?= This handwriting is mine, but I never wrote this letter.
# One shot by default; a second one: LINE2="…"
LINE2 ?=
LINES = --line "$(LINE)" $(if $(LINE2),--line "$(LINE2)")

install:
	uv sync
	git config core.hooksPath .claude/hooks

# Kokoro voice and local whisper (~620 MB of models); the voice of real mode is Kokoro too
install-local:
	uv sync --extra local

run:
	uv run --extra local python -m pipeline "$(IDEA)" $(LINES)

run-fake:
	uv run --extra local python -m pipeline "$(IDEA)" $(LINES) --mode fake

estimate:
	uv run --extra local python -m pipeline "$(IDEA)" $(LINES) --estimate

# Gate mechanics without models or network: fake + tone + echo ASR, $0
eval:
	uv run python -m evals

# The same evals on real speech: Kokoro + faster-whisper locally (make install-local), $0
eval-speech:
	uv run --extra local python -m evals --voice kokoro

# Web UI: backend :8000 + Vite :5173 (proxied to the backend). Needs Node >= 20.
# Busy ports stop with an explanation, otherwise the UI would silently use an old server.
ui:
	@uv run --extra local python -m pipeline.devcheck 8000 5173
	@test -d ui/node_modules || (cd ui && npm ci)
	@trap 'kill 0' INT TERM EXIT; \
	uv run --extra local uvicorn --factory pipeline.api:build_app --port 8000 --reload --reload-dir src \
	  --timeout-graceful-shutdown 3 & \
	cd ui && npm run dev

# A built UI is served by FastAPI itself: make ui-build, then only uvicorn -> http://localhost:8000
ui-build:
	cd ui && npm ci && npm run build

# TS types from the backend's OpenAPI, so frontend and backend cannot drift apart
ui-types:
	uv run python -c "import json; from pipeline.api import build_app; print(json.dumps(build_app().openapi()))" > ui/openapi.json
	cd ui && npx --yes openapi-typescript@7 openapi.json -o src/api-schema.ts

ui-check:
	cd ui && npm ci --silent && npx tsc -b --noEmit && npx oxlint --deny-warnings src && npm run build

test:
	uv run pytest -q

# The single definition of done: lint, UI build (strict TS, oxlint), all tests (acceptance on
# every media the UI shows, a real Chrome browser test, docs vs code, harness gates) and evals on
# real speech. .claude/.check-ok is touched only after all steps pass (the Stop hook reads it).
check: lint ui-check
	uv run --extra local pytest -q -rs
	@$(MAKE) --no-print-directory eval-speech
	@touch .claude/.check-ok

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

hooks:
	git config core.hooksPath .claude/hooks
