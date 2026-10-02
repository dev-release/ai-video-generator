from __future__ import annotations

import httpx
import httpx2
import pytest

from pipeline.config import Settings
from pipeline.nodes import Ctx
from pipeline.obs import Tracer
from pipeline.providers import Providers, fake
from pipeline.schema import Brief, PipelineState

LINE_1 = "This handwriting is mine, but I never wrote this letter."
LINE_2 = "It says I have 3 days to stop myself."


@pytest.fixture
def settings(tmp_path) -> Settings:
    # No .env and a private runs dir: tests do not depend on the machine and never write to the
    # real runs/ (the shared cache would otherwise mix tests together).
    return Settings(
        _env_file=None, pipeline_providers="fake", auto_approve=True, runs_dir=tmp_path / "runs"
    )


@pytest.fixture
def providers() -> Providers:
    return Providers(
        script=fake.FakeScriptWriter(),
        video=fake.FakeVideoGen(),
        tts=fake.ToneTTS(),
        asr=fake.EchoASR(),
    )


@pytest.fixture
def make_ctx(tmp_path, settings, providers):
    def _make(**overrides) -> Ctx:
        p = Providers(**{**providers.__dict__, **overrides})
        return Ctx(
            providers=p,
            tracer=Tracer(tmp_path, settings.max_run_cost_usd),
            settings=settings,
        )

    return _make


@pytest.fixture
def make_state(tmp_path):
    def _make(lines: tuple[str, ...] = (LINE_1, LINE_2), idea: str = "A letter from the future"):
        return PipelineState(
            run_id="test-01", run_dir=str(tmp_path), brief=Brief(idea=idea, lines=list(lines))
        )

    return _make


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path, monkeypatch):
    """Any Settings() in tests writes to a temp dir, never to the real runs/ (the shared cache
    would mix tests and litter the repo)."""
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs-default"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network or spend money: MockTransport only. A real request is a test
    error, not a silent paid API call."""

    def blocked(self, request):
        raise RuntimeError(f"network is not allowed in tests: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", blocked)
