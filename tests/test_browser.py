"""Acceptance tests in a real browser: a person opens the UI, types the text and gets a voiced
video in one start; optionally with a stop to approve the frame. They catch what API tests
cannot see: the player (video duration), JS errors, button layout. Run: `make check`
(or `uv run pytest -m browser`).

The browser is the installed Chrome (no downloads); without Chrome run `uv run playwright install
chromium`. Without either, the test is skipped with a reason (shown in the pytest report).
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from pipeline.config import ROOT, Settings

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect

TEXT = 'A girl finds a letter and whispers: "This handwriting is mine, but I never wrote it."'
TWO = 'She finds a letter and whispers: "This is mine." Then she turns the page: "Three days left."'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    import uvicorn

    from pipeline.api import build_app

    if not (ROOT / "ui" / "dist" / "index.html").exists():
        pytest.skip("UI is not built: make ui-build")
    for k in ("ANTHROPIC_API_KEY", "FAL_KEY"):
        monkeypatch.setenv(k, "")
    app = build_app(Settings(_env_file=None, runs_dir=tmp_path / "runs", fake_voice="tone"))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)
    app.state.runs.pool.shutdown(wait=True)  # finish background runs now, not at process exit


@pytest.fixture
def page(server):
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 — no system Chrome: try Playwright's Chromium
            try:
                browser = pw.chromium.launch()
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"no browser for the test: {e}".splitlines()[0])
        pg = browser.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.goto(server)
        yield pg, errors
        browser.close()


def _start(pg, *, review: bool, text: str = TEXT) -> None:
    pg.get_by_text("New run").first.wait_for()
    # Before starting, the form shows which voice will speak the line (fake: tone or real speech).
    pg.locator(".models").get_by_text("tts tone").wait_for()
    # …and that the tone placeholder speaks no words — visible before starting, without scrolling.
    expect(pg.locator(".model-note").get_by_text("without words")).to_be_in_viewport()
    pg.locator("textarea").first.fill(text)
    # The line field stays empty: the line comes from the quotes in the text (as in the task).
    pg.locator("textarea").nth(1).fill("")
    if review:
        pg.get_by_label("review before video").check()
    pg.get_by_role("button", name="Run", exact=True).click()


def _final_is_playable(pg) -> None:
    # Final video: the player must know the duration (regression: "0 s video").
    video = pg.locator("video.final").first
    video.wait_for(timeout=60_000)
    pg.get_by_text("Verbatim").first.wait_for(timeout=60_000)
    duration = pg.wait_for_function(
        "() => { const v = document.querySelector('video.final');"
        " return v && v.readyState >= 1 && v.duration > 0 ? v.duration : null }",
        timeout=20_000,
    ).json_value()
    assert 1 <= duration <= 30, duration  # not padded to 8 s (spec assemble.md)
    # The voiced video is visible right away, without scrolling below the graph.
    expect(video).to_be_in_viewport()
    # "done" only after the last stage (sync check), not just the WER gate.
    pg.locator(".badge.big.s-done").wait_for(timeout=30_000)


def test_one_run_gives_video_with_voice(page):
    """Owner scenario: type text -> one start -> a voiced video, no stops."""
    pg, errors = page
    _start(pg, review=False)
    _final_is_playable(pg)
    # Each shot's clips are shown on their stages: Video from the model, Lipsync with lips.
    for stage in ("Video", "Lipsync"):
        pg.locator(".step").filter(has=pg.get_by_text(stage, exact=True)).first.click()
        expect(pg.locator(".frames video").first).to_be_visible()
        assert pg.locator(".frames figure").count() == 1  # one line -> one shot
    assert pg.get_by_role("button", name="✓ Approve").count() == 0
    assert not errors, errors


def test_review_pauses_for_script_then_frame_approval(page):
    pg, errors = page
    _start(pg, review=True, text=TWO)
    # Stop 1, before any video: the script and the exact prompts the video model will get.
    plan = pg.locator(".script-approval")
    plan.wait_for(timeout=30_000)
    expect(plan.locator(".plan-shot")).to_have_count(2)
    expect(plan.locator(".prompt").first).to_be_in_viewport()
    expect(plan.locator(".prompt").first).to_contain_text("This is mine.")
    expect(plan.get_by_text("from the cut frame of s1")).to_be_visible()
    pg.get_by_role("button", name="✓ Approve script").click()
    # Stop 2, between shots: the end of shot 1's video next to the cut frame for comparison.
    pair = pg.locator(".cut-pair").first
    pair.wait_for(timeout=30_000)
    expect(pair.locator("video")).to_be_visible()
    expect(pair.locator("img")).to_be_visible()
    pg.get_by_role("button", name="✓ Approve").first.click(timeout=30_000)
    pg.get_by_role("button", name="Continue").click()
    _final_is_playable(pg)
    assert not errors, errors


def test_content_refusal_offers_rewrite_and_finishes(page, monkeypatch):
    """Regression (run 2026-10-02-02): the video model refused a shot's prompt; the UI showed a
    raw 422 and a resume that could never succeed. Now: what happened + a rewrite to the video."""
    from tests.test_api import refuse_once

    refuse_once(monkeypatch, shot="s2")
    pg, errors = page
    _start(pg, review=False, text=TWO)
    card = pg.locator(".rejected")
    card.wait_for(timeout=60_000)
    expect(card).to_contain_text("shot s2")
    expect(card).to_be_in_viewport()
    assert pg.get_by_role("button", name="Resume from where it stopped").count() == 0
    expect(pg.get_by_role("button", name="↻ New take of s2")).to_be_visible()
    pg.get_by_role("button", name="✎ Rewrite script").click()
    _final_is_playable(pg)
    expect(card).to_have_count(0)
    assert not errors, errors


def test_run_again_from_the_start_after_any_error(page, monkeypatch):
    """Owner request 2026-10-02: one button to run the whole process again when a stage fails —
    the form opens with the same text, the person presses Run and gets the video."""
    from tests.test_api import refuse_once

    refuse_once(monkeypatch, shot="s2")
    pg, errors = page
    _start(pg, review=False, text=TWO)
    pg.locator(".rejected").wait_for(timeout=60_000)
    pg.get_by_role("button", name="↻ Run again from the start").click()
    expect(pg.locator("textarea").first).to_have_value(TWO)
    expect(pg.get_by_label("review before video")).not_to_be_checked()
    pg.get_by_role("button", name="Run", exact=True).click()
    _final_is_playable(pg)
    assert not errors, errors
