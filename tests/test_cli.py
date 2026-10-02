"""CLI: clear errors before spending, exit codes, a fake run in one command."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.__main__ import main
from tests.conftest import LINE_1


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    # Independent of the owner's .env: empty keys, a private runs dir.
    for k in ("ANTHROPIC_API_KEY", "FAL_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FAKE_VOICE", "tone")


def test_line_with_tag_rejected_before_anything(capsys):
    assert main(["idea here", "--line", "[whispers] I never wrote this.", "--mode", "fake"]) == 2


def test_missing_lines_is_usage_error():
    assert main(["idea only", "--mode", "fake"]) == 2


def test_real_mode_without_keys_lists_all_problems(capsys):
    code = main(["A letter", "--line", LINE_1, "--mode", "real", "--estimate"])
    err = capsys.readouterr().err
    assert code == 2
    for key in ("ANTHROPIC_API_KEY", "FAL_KEY"):
        assert key in err


def test_estimate_fake_is_free(capsys):
    assert main(["A letter", "--line", LINE_1, "--mode", "fake", "--estimate"]) == 0


def test_fake_run_produces_final(tmp_path):
    assert main(["A letter", "--line", LINE_1, "--mode", "fake"]) == 0
    assert list(Path(tmp_path, "runs").glob("*/final.mp4"))


def test_smoke_without_key_leaves_no_phantom_spend(tmp_path):
    from pipeline.config import settings
    from pipeline.smoke import main as smoke
    from pipeline.voices import ConfigError

    settings.cache_clear()
    with pytest.raises(ConfigError):
        smoke(["script", "--yes"])
    assert not (tmp_path / "runs" / "_ledger.jsonl").exists()
    settings.cache_clear()


def test_smoke_without_yes_spends_nothing(tmp_path):
    from pipeline.config import settings
    from pipeline.smoke import main as smoke

    settings.cache_clear()
    assert smoke(["video"]) == 2
    assert not (tmp_path / "runs" / "_ledger.jsonl").exists()
    settings.cache_clear()


def test_single_text_with_quoted_line(tmp_path):
    text = 'A girl finds a letter and whispers: "This handwriting is mine, but I never wrote it."'
    assert main([text, "--mode", "fake"]) == 0
    brief = next(Path(tmp_path, "runs").glob("*/brief.json")).read_text()
    assert "This handwriting is mine, but I never wrote it." in brief


def test_default_is_fully_automatic(tmp_path, monkeypatch):
    # The task: "the pipeline is automatic: one command — a video file at the output".
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("the CLI must not ask"))
    assert main(['A letter "I never wrote this letter."', "--mode", "fake"]) == 0
    assert list(Path(tmp_path, "runs").glob("*/final.mp4"))


def test_review_waits_for_human(tmp_path, monkeypatch):
    # The pause is between shots (spec keyframe.md): two lines.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    text = 'A letter "I never wrote this letter." Then: "Who sent it?"'
    assert main([text, "--mode", "fake", "--review"]) == 3
    assert not list(Path(tmp_path, "runs").glob("*/final.mp4"))


def test_empty_enter_does_not_regenerate(monkeypatch):
    from pipeline.__main__ import _ask
    from pipeline.graph import RunResult

    answers = iter(["", "  ", "a"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    frame = {"shot_id": "s2", "path": "runs/r/keyframes/s2.png", "source_shot": "s1", "at_s": 3.86}
    res = RunResult("r", "waiting_approval", None,
                    interrupt={"kind": "approve_cut_frames", "frames": [frame],
                               "next_cost_usd": 0.41})  # fmt: skip
    assert _ask(res) == {"s2": "approve"}


def test_regen_requires_run_id():
    assert (
        main(['A letter "I never wrote this letter."', "--mode", "fake", "--regen", "voice:s1"])
        == 2
    )


def test_regen_makes_a_new_take(tmp_path):
    import json

    assert main(['A letter "I never wrote this letter."', "--mode", "fake"]) == 0
    run = next(Path(tmp_path, "runs").glob("2026-*"))
    seed_before = json.loads((run / "voice" / "s1.request.json").read_text())["seed"]
    assert main(["--run-id", run.name, "--regen", "voice:s1", "--mode", "fake"]) == 0
    seed_after = json.loads((run / "voice" / "s1.request.json").read_text())["seed"]
    assert seed_after != seed_before


def test_script_pause_prints_the_exact_video_prompt_and_can_rewrite(monkeypatch, capsys):
    from pipeline.__main__ import _ask
    from pipeline.graph import RunResult

    answers = iter(["", "r"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    script = {
        "title": "Letter",
        "style": "noir",
        "characters": [{"id": "mia", "look": "young woman", "voice_profile": "female_young_warm"}],
    }
    shot = {
        "shot_id": "s1",
        "character_id": "mia",
        "line": "I never wrote this.",
        "delivery": "neutral",
        "start": "text",
        "source_shot": None,
        "shot_s": 2.4,
        "clip_s": 3,
        "prompt": "PROMPT-S1",
    }
    plan = {"model": "kling", "negative_prompt": "text", "cost_usd": 0.3, "shots": [shot]}
    pause = {"kind": "approve_script", "script": script, "plan": plan, "next_cost_usd": 0.3}
    res = RunResult("r", "waiting_approval", None, interrupt=pause)
    assert _ask(res) == {"script": "regenerate"}  # an empty Enter does not count
    err = capsys.readouterr().err
    assert "PROMPT-S1" in err and "I never wrote this." in err
