"""Spending limits: run cap, daily cap, call limit, prices, retries on 5xx only."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from pipeline import pricing, registry
from pipeline.config import MAX_CALLS_PER_UNIT, Settings
from pipeline.obs import BudgetExceeded, CallLimitExceeded, Ledger, Tracer
from pipeline.retry import is_retryable_read, is_retryable_submit, submit_retry
from pipeline.voices import ConfigError


def test_run_ceiling_blocks_before_call(tmp_path):
    t = Tracer(tmp_path, max_cost_usd=1.0)
    t.charge("video", 0.8, "clip")
    with pytest.raises(BudgetExceeded):
        t.charge("video", 0.3, "another clip")
    assert t.cost_usd == pytest.approx(0.8)


def test_daily_ceiling_across_runs(tmp_path):
    ledger = Ledger(tmp_path / "_ledger.jsonl")
    Tracer(tmp_path / "r1", 5.0, max_daily_usd=1.0, ledger=ledger).charge("video", 0.9, "a")
    with pytest.raises(BudgetExceeded, match="MAX_DAILY"):
        Tracer(tmp_path / "r2", 5.0, max_daily_usd=1.0, ledger=ledger).charge("video", 0.2, "b")


def test_call_limit_per_node(tmp_path):
    t = Tracer(tmp_path, max_cost_usd=100)
    for _ in range(MAX_CALLS_PER_UNIT["script"]):
        t.charge("script", 0.01, "s")
    with pytest.raises(CallLimitExceeded):
        t.charge("script", 0.01, "s")


def test_call_limit_is_per_unit_so_ten_shots_fit_but_a_loop_on_one_clip_stops(tmp_path):
    """Up to 10 shots: a per-run limit would stop the 5th shot; a per-artifact limit lets 10
    shots through and still catches a loop on one clip."""
    t = Tracer(tmp_path, max_cost_usd=100)
    for i in range(1, 11):
        t.charge("video", 0.3, f"s{i}", unit=f"s{i}.mp4")
    for _ in range(MAX_CALLS_PER_UNIT["video"] - 1):
        t.charge("video", 0.3, "s1 again", unit="s1.mp4")
    with pytest.raises(CallLimitExceeded, match="s1.mp4"):
        t.charge("video", 0.3, "s1 loop", unit="s1.mp4")
    restored = Tracer(tmp_path, max_cost_usd=100)  # after a crash the counter is the same
    with pytest.raises(CallLimitExceeded):
        restored.charge("video", 0.3, "s1 loop", unit="s1.mp4")
    restored.charge("video", 0.3, "s2 again", unit="s2.mp4")


def test_free_calls_do_not_count(tmp_path):
    t = Tracer(tmp_path, max_cost_usd=0.01)
    for _ in range(20):
        t.charge("voice", 0.0, "kokoro")
    assert t.cost_usd == 0


def test_cost_and_calls_restored_after_crash(tmp_path):
    Tracer(tmp_path, 5.0).charge("video", 1.5, "a")
    t = Tracer(tmp_path, 5.0)
    assert t.cost_usd == pytest.approx(1.5) and sum(t.calls.values()) == 1


@pytest.mark.parametrize("cap", [5.01, 50])
def test_run_ceiling_cannot_be_raised_above_5(cap):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_run_cost_usd=cap)


def test_daily_ceiling_cannot_be_raised_above_20():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_daily_cost_usd=25)


def test_paid_model_without_price_source_is_rejected():
    # A paid model without a price source would bypass the cost cap; the registry rejects it.
    with pytest.raises(ValidationError, match="source"):
        registry.ModelSpec(name="x", capability="video", transport="fal", endpoint="e",
                           output="video.url", key="FAL_KEY", durations_s=[5],
                           price={"unit": "second", "usd": 0.1})  # fmt: skip


def test_unknown_model_is_config_error_with_known_names():
    with pytest.raises(ConfigError, match="kling-v3-std"):
        registry.get("video", "no-such-model")


def test_prices():
    tts = registry.get("tts", "kokoro")
    tts = tts.model_copy(update={"price": tts.price.model_copy(update={"usd": 0.08})})
    kling = registry.get("video", "kling-v3-std")
    assert pricing.cost(registry.get("video", "fake"), seconds=5) == 0
    assert pricing.cost(tts, chars=1000) == pytest.approx(0.08)
    assert pricing.cost(kling, seconds=5) == pytest.approx(5 * kling.price.usd)


def test_bill_step_rounds_up_from_spec():
    ks = registry.get("lipsync", "kling-lipsync")  # billed in 5 s steps, from the model price
    assert pricing.cost(ks, seconds=3.2) == pytest.approx(5 * ks.price.usd)
    assert pricing.cost(ks, seconds=5.0) == pytest.approx(5 * ks.price.usd)


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


CODES = [(500, True), (503, True), (400, False), (401, False), (404, False), (422, False),
         (429, False)]  # fmt: skip


@pytest.mark.parametrize(("code", "retry"), CODES)
def test_status_codes_same_for_submit_and_read(code, retry):
    assert is_retryable_submit(_status_error(code)) is retry
    assert is_retryable_read(_status_error(code)) is retry


def test_read_timeout_retried_only_for_idempotent_reads():
    # A paid request may have run before the timeout: a retry would pay twice.
    assert is_retryable_read(httpx.ReadTimeout("t")) is True
    assert is_retryable_submit(httpx.ReadTimeout("t")) is False


def test_connect_errors_retried_for_submit():
    assert is_retryable_submit(httpx.ConnectError("no route")) is True
    assert is_retryable_submit(httpx.ConnectTimeout("t")) is True


def test_anthropic_timeout_not_retried_for_submit():
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api")
    assert is_retryable_submit(anthropic.APITimeoutError(request=req)) is False
    assert is_retryable_submit(anthropic.APIConnectionError(request=req)) is True


def test_4xx_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls = []

    @submit_retry
    def call():
        calls.append(1)
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        call()
    assert len(calls) == 1


def test_failed_ffmpeg_leaves_no_partial_output(tmp_path):
    from pipeline.media import FfmpegError, run_ffmpeg

    out = tmp_path / "final.mp4"
    with pytest.raises(FfmpegError):
        run_ffmpeg(["-f", "lavfi", "-i", "nosuchfilter=x", str(out)])
    assert not out.exists() and not list((tmp_path / ".tmp").glob("*"))


def test_ffmpeg_output_appears_only_complete(tmp_path, monkeypatch):
    import pipeline.media as media

    out = tmp_path / "a.wav"
    seen: list[bool] = []
    real = media._ffmpeg

    def spy(args):
        seen.append(out.exists())  # while writing, the target does not exist yet
        return real(args)

    monkeypatch.setattr(media, "_ffmpeg", spy)
    media.run_ffmpeg(["-f", "lavfi", "-i", "sine=d=0.2", str(out)])
    assert seen == [False] and out.exists()


# ---------- Tracer.spend: reserve, refund, actual cost, resume ----------


class Prov:
    def __init__(self, ready_error=None):
        self.ready_error, self.last_cost_usd = ready_error, None

    def ready(self):
        if self.ready_error:
            raise self.ready_error


def _post_error(code):
    req = httpx.Request("POST", "https://x")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


def tracer(tmp_path, **kw):
    return Tracer(
        tmp_path / "run", 5.0, max_daily_usd=5.0, ledger=Ledger(tmp_path / "l.jsonl"), **kw
    )


def test_missing_key_reserves_nothing(tmp_path):
    t = tracer(tmp_path)
    with (
        pytest.raises(ConfigError),
        t.spend("video", Prov(ConfigError("no key")), 0.4, "x", key="k"),
    ):
        pass
    assert t.cost_usd == 0 and Ledger(tmp_path / "l.jsonl").spent_today() == 0


def test_rejected_request_is_refunded(tmp_path):
    t = tracer(tmp_path)
    with pytest.raises(httpx.HTTPStatusError), t.spend("video", Prov(), 0.4, "x", key="k"):
        raise _post_error(422)
    assert t.cost_usd == pytest.approx(0) and t.calls["video"] == 0
    assert Ledger(tmp_path / "l.jsonl").spent_today() == pytest.approx(0)


def test_ambiguous_failure_keeps_reserve_and_next_attempt_reserves_again(tmp_path):
    t = tracer(tmp_path)
    with pytest.raises(httpx.ReadTimeout), t.spend("video", Prov(), 0.4, "x", key="k"):
        raise httpx.ReadTimeout("t")
    assert t.cost_usd == pytest.approx(0.4)  # the provider may have billed: count it
    with t.spend("video", Prov(), 0.4, "x", key="k"):
        pass
    assert t.cost_usd == pytest.approx(0.8)


def test_still_running_job_is_not_reserved_twice_even_after_restart(tmp_path):
    from pipeline.providers.fal import FalStillRunning

    t = tracer(tmp_path)
    with pytest.raises(FalStillRunning), t.spend("video", Prov(), 0.4, "x", key="k"):
        raise FalStillRunning("still running")
    t2 = tracer(tmp_path)  # a new process: state from trace.jsonl
    with t2.spend("video", Prov(), 0.4, "x", key="k"):
        pass
    assert t2.cost_usd == pytest.approx(0.4) and t2.calls["video"] == 1


def test_actual_cost_settles_the_reserve(tmp_path):
    t = tracer(tmp_path)
    p = Prov()
    with t.spend("script", p, 0.10, "x", key="k"):
        p.last_cost_usd = 0.031
    assert t.cost_usd == pytest.approx(0.031)
    assert Ledger(tmp_path / "l.jsonl").spent_today() == pytest.approx(0.031)
    assert tracer(tmp_path).cost_usd == pytest.approx(0.031)  # restore takes the last record
