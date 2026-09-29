.PHONY: install run run-fake eval test lint fmt hooks

IDEA ?= A girl finds a letter from her future self

install:
	uv sync --all-extras
	git config core.hooksPath .claude/hooks

run:
	uv run python -m pipeline "$(IDEA)"

run-fake:
	PIPELINE_PROVIDERS=fake uv run python -m pipeline "$(IDEA)"

eval:
	uv run python -m evals

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

hooks:
	git config core.hooksPath .claude/hooks
