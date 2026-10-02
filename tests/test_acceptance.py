"""Acceptance tests: what a person sees, not the internal logic.

They walk user paths through the API (as the UI does) and on EVERY poll check EVERY media the
UI can show: the file is complete, decodes, has frames/audio; the final video is <= 30 s without
a silent tail, 9:16, audible, with the index first (plays in the browser at once). Regressions
this catches: a 0 s video (partial file), a silent final video, a broken frame.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.api import build_app
from pipeline.config import MAX_FINAL_S, Settings
from pipeline.media import audio_envelope, ffmpeg_exe, probe

TEXT = 'A girl finds a letter and whispers: "This handwriting is mine, but I never wrote it."'
# The approval pause is only between shots (spec keyframe.md): two lines are needed.
TWO = 'She finds a letter and whispers: "This is mine." Then she turns the page: "Three days left."'


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "FAL_KEY"):
        monkeypatch.setenv(k, "")
    s = Settings(_env_file=None, runs_dir=tmp_path / "runs", fake_voice="tone")
    app = build_app(s)
    yield TestClient(app)
    # Wait for background runs, otherwise they keep running after the test until process exit.
    app.state.runs.pool.shutdown(wait=True)


def decodes(path: Path) -> None:
    """Full decode without errors, the way a player does it."""
    r = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and not r.stderr.strip(), f"{path.name}: {r.stderr[-300:]}"


def moov_first(path: Path) -> bool:
    """mp4 index before the data: the browser knows the duration without loading the whole file."""
    data = path.read_bytes()
    moov, mdat = data.find(b"moov"), data.find(b"mdat")
    return 0 <= moov < mdat


def fetch(client, url: str, tmp: Path) -> Path:
    r = client.get(url)
    assert r.status_code == 200, url
    out = tmp / Path(url.split("?")[0]).name
    out.write_bytes(r.content)
    return out


def check_visible_media(client, view: dict, tmp: Path) -> None:
    """Every media the UI can show right now is complete and playable."""
    a = view["artifacts"]
    for v in a["voices"]:
        p = fetch(client, v["url"], tmp)
        assert probe(p).duration_s > 0.3 and max(audio_envelope(p, 30)) > 0
    for k in a["keyframes"]:
        p = fetch(client, k["url"], tmp)
        pr = probe(p)
        assert pr.width and pr.height and abs(pr.width / pr.height - 9 / 16) < 0.01
    for c in a["clips"]:
        p = fetch(client, c["url"], tmp)
        assert probe(p).duration_s > 0
        decodes(p)
    if a["final"]:
        p = fetch(client, a["final"], tmp)
        pr = probe(p)
        assert 1 <= pr.duration_s <= MAX_FINAL_S, pr  # not padded with silence (spec assemble.md)
        assert (pr.width, pr.height) == (1080, 1920) and pr.has_video and pr.has_audio
        assert moov_first(p), "no faststart: the browser shows 0 s until fully loaded"
        decodes(p)
        env = audio_envelope(p, 30)
        assert sum(v > 0.1 for v in env) > 10, "final video is silent"


def approve_all(interrupt: dict) -> dict[str, str]:
    if interrupt["kind"] == "approve_script":
        return {"script": "approve"}
    return {f["shot_id"]: "approve" for f in interrupt["frames"]}


def run_and_watch(client, tmp: Path, body: dict, approve: bool = False, t: float = 90) -> dict:
    run_id = client.post("/api/runs", json=body).json()["run_id"]
    deadline, polls, approved = time.time() + t, 0, 0
    while time.time() < deadline:
        view = client.get(f"/api/runs/{run_id}").json()
        check_visible_media(client, view, tmp)
        polls += 1
        if view["status"] == "waiting_approval" and approve:
            approved += 1
            client.post(f"/api/runs/{run_id}/approve",
                        json={"decisions": approve_all(view["interrupt"])})  # fmt: skip
        elif view["status"] not in ("queued", "running", "waiting_approval"):
            return {**view, "_polls": polls, "_approved": approved}
        time.sleep(0.05)
    raise TimeoutError(view["status"])


def test_one_text_to_playable_video_with_human_approval(client, tmp_path):
    view = run_and_watch(client, tmp_path, {"idea": TWO, "lines": [], "mode": "fake"}, approve=True)
    assert view["status"] == "done", view["error"]
    # Two stops, both passed by a human: the script with video prompts, the cut frame between shots.
    assert view["_approved"] == 2
    assert view["asr"]["passed"] and all(c["passed"] for c in view["voice_checks"])
    assert all(n["status"] == "done" for n in view["nodes"])
    assert view["_polls"] > 3  # media were checked mid-run too, not only at the end


def test_two_shots_to_playable_video(client, tmp_path):
    body = {"idea": 'Two shots. "I found it in May." Then: "It says I have 3 days."', "lines": [],
            "mode": "fake", "auto_approve": True}  # fmt: skip
    view = run_and_watch(client, tmp_path, body)
    assert view["status"] == "done", view["error"]
    assert len(view["artifacts"]["clips"]) == 4  # a clip + a lipsync clip per shot


def test_regenerate_keeps_everything_playable(client, tmp_path):
    view = run_and_watch(client, tmp_path, {"idea": TEXT, "lines": [], "mode": "fake",
                                            "auto_approve": True})  # fmt: skip
    rid = view["run_id"]
    client.post(f"/api/runs/{rid}/regenerate", json={"node": "voice", "shot_id": "s1"})
    deadline = time.time() + 60
    while time.time() < deadline:
        v = client.get(f"/api/runs/{rid}").json()
        check_visible_media(client, v, tmp_path)
        if v["status"] == "done":
            break
        time.sleep(0.05)
    assert v["status"] == "done"


def test_user_errors_are_explained_not_crashes(client):
    r = client.post(
        "/api/runs", json={"idea": "A girl finds a letter", "lines": [], "mode": "fake"}
    )
    assert r.status_code == 422 and "quotes" in str(r.json()["detail"])
    r = client.post(
        "/api/runs", json={"idea": 'x "[whispers] no way."', "lines": [], "mode": "fake"}
    )
    assert r.status_code == 422
    r = client.post("/api/runs", json={"idea": TEXT, "lines": [], "mode": "real"})
    assert r.status_code == 409 and r.json()["detail"]["problems"]
