"""Web API: the same logic as the CLI, state rebuilt from artifacts. Spec: .claude/specs/ui.md."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.api import build_app
from pipeline.config import Settings
from tests.conftest import LINE_1, LINE_2

BODY = {"idea": "A letter from the future", "lines": [LINE_1, LINE_2], "mode": "fake"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "FAL_KEY"):
        monkeypatch.setenv(k, "")
    s = Settings(_env_file=None, runs_dir=tmp_path / "runs", fake_voice="tone")
    app = build_app(s)
    yield TestClient(app)
    # Wait for background runs, otherwise they keep running after the test until process exit.
    app.state.runs.pool.shutdown(wait=True)


def wait(client, run_id, until=("done", "failed_verify", "error", "waiting_approval"), t=60):
    deadline = time.time() + t
    while time.time() < deadline:
        v = client.get(f"/api/runs/{run_id}").json()
        if v["status"] in until:
            return v
        time.sleep(0.2)
    raise TimeoutError(v["status"])


def test_limits_come_from_python(client):
    lim = client.get("/api/limits").json()
    assert lim["max_words_total"] == 60 and "whispers" in lim["deliveries"]
    assert lim["max_lines"] == 10
    assert lim["max_run_cost_usd"] <= 5


def test_fake_voice_placeholder_is_explained_before_run(client):
    # A fake run without Kokoro hums instead of speaking: the form must say so up front.
    notes = client.get("/api/limits").json()["notes_by_mode"]
    assert any("without words" in n and "FAKE_VOICE=kokoro" in n for n in notes["fake"])


def test_each_shot_is_listed_once_per_stage(client):
    # Regression: the UI showed technical s1.lip/s1.shot files as "shots" with a regenerate button.
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    a = wait(client, run_id, until=("done",))["artifacts"]
    assert [v["shot_id"] for v in a["voices"]] == ["s1", "s2"]
    stages = {(c["shot_id"], c["stage"]) for c in a["clips"]}
    assert stages == {(s, st) for s in ("s1", "s2") for st in ("video", "lipsync")}


def test_ten_shots_are_listed_in_shot_order(client):
    # Up to 10 shots: string sorting of files would show s1, s10, s2.
    from tests.test_approve_script import TEN

    body = {"idea": "A girl reads a letter", "lines": TEN, "mode": "fake", "auto_approve": True}
    run_id = client.post("/api/runs", json=body).json()["run_id"]
    v = wait(client, run_id, until=("done", "error", "failed_verify"), t=180)
    assert v["status"] == "done", v["error"]
    order = [f"s{i}" for i in range(1, 11)]
    a = v["artifacts"]
    assert [x["shot_id"] for x in a["voices"]] == order
    assert [k["shot_id"] for k in a["keyframes"]] == order[1:]
    assert [c["shot_id"] for c in a["clips"] if c["stage"] == "video"] == order
    assert [c["shot_id"] for c in v["voice_checks"]] == order


def test_bad_line_is_422_with_field(client):
    r = client.post("/api/runs", json={**BODY, "lines": ["[whispers] no way out."]})
    assert r.status_code == 422
    assert "lines" in str(r.json()["detail"])


def test_real_without_keys_is_409_with_all_problems(client):
    r = client.post("/api/runs", json={**BODY, "mode": "real"})
    assert r.status_code == 409
    problems = " ".join(r.json()["detail"]["problems"])
    assert "ANTHROPIC_API_KEY" in problems and "FAL_KEY" in problems


def test_preflight_estimate(client):
    r = client.post("/api/preflight", json=BODY).json()
    assert r["ok"] and r["estimate_usd"] == 0


def test_full_flow_with_human_approval(client):
    run_id = client.post("/api/runs", json=BODY).json()["run_id"]
    v = wait(client, run_id)
    # Pause 1: the script and the exact prompts the video model will get; no video yet.
    assert v["status"] == "waiting_approval" and v["interrupt"]["kind"] == "approve_script"
    plan = v["interrupt"]["plan"]
    assert [s["shot_id"] for s in plan["shots"]] == ["s1", "s2"]
    assert [s["line"] for s in plan["shots"]] == [LINE_1, LINE_2]
    assert [s["start"] for s in plan["shots"]] == ["text", "cut_frame"]
    assert all(s["line"] in s["prompt"] for s in plan["shots"])
    assert v["interrupt"]["script"]["shots"][0]["line"] == LINE_1
    assert v["artifacts"]["clips"] == []
    wrong = client.post(f"/api/runs/{run_id}/approve", json={"decisions": {"s2": "approve"}})
    assert wrong.status_code == 422  # a decision for another pause does not get stuck in error
    client.post(f"/api/runs/{run_id}/approve", json={"decisions": {"script": "approve"}})
    time.sleep(0.3)
    v = wait(client, run_id)
    assert v["status"] == "waiting_approval"
    assert {n["name"]: n["status"] for n in v["nodes"]}["approve"] == "waiting"
    # Pause between shots: cut frame of s2 from clip s1; clip s1 is visible for comparison.
    [f] = v["interrupt"]["frames"]
    assert (f["shot_id"], f["source_shot"]) == ("s2", "s1")
    [k] = v["artifacts"]["keyframes"]
    assert k["url"].startswith(f"/media/{run_id}/keyframes/s2.png") and k["source_shot"] == "s1"
    assert {(c["shot_id"], c["stage"]) for c in v["artifacts"]["clips"]} == {("s1", "video")}

    client.post(f"/api/runs/{run_id}/approve", json={"decisions": {"s2": "approve"}})
    v = wait(client, run_id, until=("done", "failed_verify", "error"))
    assert v["status"] == "done", v["error"]
    assert v["asr"]["passed"] and v["artifacts"]["final"].startswith(
        f"/media/{run_id}/final.mp4?v="
    )
    assert all(n["status"] == "done" for n in v["nodes"])
    assert client.get(v["artifacts"]["final"]).status_code == 200
    assert [r["run_id"] for r in client.get("/api/runs").json()] == [run_id]


def test_regenerate_voice_keeps_frames(client, tmp_path):
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    wait(client, run_id)
    frame = tmp_path / "runs" / run_id / "keyframes" / "s2.png"
    before = frame.stat().st_mtime_ns
    r = client.post(f"/api/runs/{run_id}/regenerate", json={"node": "voice", "shot_id": "s2"})
    assert r.status_code == 202
    v = wait(client, run_id, until=("done",))
    voice = {n["name"]: n for n in v["nodes"]}["voice"]
    assert voice["runs"] == 2 and frame.stat().st_mtime_ns == before


def test_unknown_run_is_404_and_no_path_traversal(client):
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404


def test_copied_run_viewable_without_checkpoint(client, tmp_path):
    d = tmp_path / "runs" / "copied-run"
    d.mkdir(parents=True)
    (d / "brief.json").write_text('{"idea": "x x x", "lines": ["I never wrote this."]}')
    (d / "asr_check.json").write_text('{"passed": true, "wer": 0}')
    v = client.get("/api/runs/copied-run").json()
    assert v["status"] == "done"
    assert Path(d).exists()


def test_media_url_version_follows_file_change(client, tmp_path):
    """A rewritten file (regeneration) -> another URL, otherwise the browser shows a cached one."""
    import os

    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    before = wait(client, run_id, until=("done",))["artifacts"]["final"]
    final = tmp_path / "runs" / run_id / "final.mp4"
    st = final.stat()
    os.utime(final, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    after = client.get(f"/api/runs/{run_id}").json()["artifacts"]["final"]
    assert before != after and after.split("?")[0] == before.split("?")[0]


def test_final_is_never_visible_half_written(client, tmp_path):
    """Every time the API links final.mp4, the file must be complete (regression: a 0 s video)."""
    from pipeline.media import probe

    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    seen = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        v = client.get(f"/api/runs/{run_id}").json()
        if v["artifacts"]["final"]:
            p = probe(tmp_path / "runs" / run_id / "final.mp4")
            assert p.duration_s > 1 and p.has_video  # a partial file shows as 0 s
            seen += 1
        if v["status"] == "done":
            break
        time.sleep(0.01)
    assert seen >= 1


def test_approval_pause_shows_next_cost(client):
    run_id = client.post("/api/runs", json=BODY).json()["run_id"]
    v = wait(client, run_id)
    assert v["status"] == "waiting_approval"
    assert v["interrupt"]["next_cost_usd"] == 0  # fake is $0; in real mode, video + lipsync


def test_new_take_of_shot1_asks_again_for_the_cut_frame(client):
    run_id = client.post("/api/runs", json=BODY).json()["run_id"]
    wait(client, run_id)
    client.post(f"/api/runs/{run_id}/approve", json={"decisions": {"script": "approve"}})
    time.sleep(0.3)
    wait(client, run_id)
    client.post(f"/api/runs/{run_id}/approve", json={"decisions": {"s2": "approve"}})
    wait(client, run_id, until=("done",))
    # A cut frame is not regenerated on its own: it is a frame of the previous shot's video.
    r = client.post(f"/api/runs/{run_id}/regenerate", json={"node": "keyframe", "shot_id": "s2"})
    assert r.status_code == 422
    client.post(f"/api/runs/{run_id}/regenerate", json={"node": "video", "shot_id": "s1"})
    time.sleep(0.3)
    v = wait(client, run_id)
    assert v["status"] == "waiting_approval"
    assert [f["shot_id"] for f in v["interrupt"]["frames"]] == ["s2"]


def test_resume_endpoint_continues_a_run(client):
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    wait(client, run_id, until=("done",))
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 202
    assert wait(client, run_id, until=("done",))["status"] == "done"


def test_fresh_run_is_marked(client, tmp_path):
    import json as _json

    run_id = client.post("/api/runs", json={**BODY, "fresh": True}).json()["run_id"]
    takes = _json.loads((tmp_path / "runs" / run_id / "takes.json").read_text())
    assert takes["salt"] == run_id


def test_run_from_an_older_version_is_viewable_not_500(client, monkeypatch):
    # Regression: a checkpoint with a removed voice profile gave a 500 on every UI poll. Now the
    # run is viewable from artifacts with an explanation.
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    wait(client, run_id, until=("done",))

    class OldCheckpoint:
        @staticmethod
        def model_validate(_):
            raise AttributeError("'dict' object has no attribute 'id'")

    monkeypatch.setattr("pipeline.graph.PipelineState", OldCheckpoint)
    r = client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    v = r.json()
    assert v["status"] == "done" and "older pipeline version" in v["error"]
    assert v["artifacts"]["final"]  # the finished video is still visible


def refuse_once(monkeypatch, shot="s2"):
    """The video model refuses one shot once, as Kling did in run 2026-10-02-02."""
    from pipeline.providers import ContentRejected
    from pipeline.providers.fake import FakeVideoGen

    real, refused = FakeVideoGen.animate, []

    def animate(self, image, motion, duration_s, out, seed=0):
        if out.stem == shot and not refused:
            refused.append(out.name)
            raise ContentRejected("kling: the model's content checker refused the request")
        return real(self, image, motion, duration_s, out, seed)

    monkeypatch.setattr(FakeVideoGen, "animate", animate)


def test_content_refusal_names_the_shot_and_rewrite_finishes_the_run(client, monkeypatch):
    refuse_once(monkeypatch)
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    v = wait(client, run_id)
    assert v["status"] == "error"
    assert v["rejected"] == {"node": "video", "shot_id": "s2"}
    assert client.post(f"/api/runs/{run_id}/rewrite").status_code == 202
    time.sleep(0.3)
    # Started without review -> the rewrite also runs without stops, to the video.
    v = wait(client, run_id, until=("done", "failed_verify", "error"))
    assert v["status"] == "done", v["error"]
    assert v["rejected"] is None and v["error"] is None


def test_plain_error_is_not_a_content_refusal(client, monkeypatch):
    from pipeline.providers.fake import FakeVideoGen

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(FakeVideoGen, "animate", boom)
    run_id = client.post("/api/runs", json={**BODY, "auto_approve": True}).json()["run_id"]
    v = wait(client, run_id)
    assert v["status"] == "error" and v["rejected"] is None
