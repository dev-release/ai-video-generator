"""Real providers on transport mocks: no network, no keys. 200 / 5xx (retry) / 4xx (stop)."""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx
import httpx2
import pytest

from pipeline import registry
from pipeline.config import Settings
from pipeline.providers import claude, fal
from pipeline.providers.fake import FakeScriptWriter
from pipeline.schema import Brief, ScriptDraft
from pipeline.voices import ConfigError
from tests.conftest import LINE_1, LINE_2

BRIEF = Brief(idea="A letter from the future", lines=[LINE_1, LINE_2])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, pipeline_providers="real", **kw)


# ---------- Claude ----------


def _message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def claude_with(responses: list[tuple[int, dict]]):
    seen: list[dict] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(req.content))
        code, body = responses[min(len(seen), len(responses)) - 1]
        return httpx2.Response(code, json=body)

    client = anthropic.Anthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return claude.ClaudeScriptWriter(
        registry.get("script", "claude-opus-5-5"), settings(), client=client
    ), seen


def _draft_json() -> str:
    return FakeScriptWriter().write(BRIEF, None).model_dump_json()


def test_claude_returns_draft_and_never_sends_line_field():
    writer, seen = claude_with([(200, _message(_draft_json()))])
    draft = writer.write(BRIEF, None)
    assert len(draft.shots) == 2
    req = seen[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default"
    schema = req["output_config"]["format"]["schema"]
    assert '"line"' not in json.dumps(schema)
    assert LINE_1 in req["messages"][0]["content"]  # the lines are in the prompt as context


def test_claude_5xx_is_retried_then_succeeds():
    err = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    writer, seen = claude_with([(529, err), (500, err), (200, _message(_draft_json()))])
    assert writer.write(BRIEF, None).title
    assert len(seen) == 3


def test_claude_4xx_is_not_retried():
    err = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    writer, seen = claude_with([(400, err)])
    with pytest.raises(anthropic.BadRequestError):
        writer.write(BRIEF, None)
    assert len(seen) == 1


def test_claude_429_is_not_retried():
    err = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
    writer, seen = claude_with([(429, err)])
    with pytest.raises(anthropic.RateLimitError):
        writer.write(BRIEF, None)
    assert len(seen) == 1


def test_claude_refusal_is_explicit_error():
    writer, _ = claude_with([(200, _message("", stop_reason="refusal"))])
    with pytest.raises(claude.ScriptRefused):
        writer.write(BRIEF, None)


def test_claude_feedback_goes_into_prompt():
    writer, seen = claude_with([(200, _message(_draft_json()))])
    writer.write(BRIEF, "shots: expected 2")
    assert "shots: expected 2" in seen[0]["messages"][0]["content"]


def test_claude_without_key_is_config_error_only_on_use():
    w = claude.ClaudeScriptWriter(registry.get("script", "claude-opus-5-5"), settings())  # no key
    with pytest.raises(ConfigError):
        w.write(BRIEF, None)


def test_output_schema_is_strict():
    s = claude.output_schema()
    for obj in [s, *s["$defs"].values()]:
        if obj.get("type") == "object":
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])


def test_output_schema_keeps_every_model_field():
    # A live smoke once lost the `title` field from the schema (stripped as a JSON Schema
    # keyword): Claude did not return it and ScriptDraft failed. The mock did not see it.
    from pipeline.schema import Character, DraftShot

    s = claude.output_schema()
    assert set(s["properties"]) == set(ScriptDraft.model_fields)
    for model in (Character, DraftShot):
        assert set(s["$defs"][model.__name__]["properties"]) == set(model.model_fields)
    assert "title" not in s["properties"]["title"]  # the keyword is stripped, the field is not


# ---------- fal: a job is submitted exactly once ----------


class FalMock:
    """fal queue: submit -> status -> result -> file. Handles transient 5xx and a submit timeout."""

    def __init__(self, result: dict, submit=(200,), statuses=("IN_PROGRESS", "COMPLETED"),
                 result_codes=(200,), download_codes=(200,), submit_timeout=False,
                 error_body=None):  # fmt: skip
        self.result, self.statuses = result, list(statuses)
        self.error_body = error_body or {"detail": "x"}
        self.submit_codes, self.result_codes = list(submit), list(result_codes)
        self.download_codes, self.submit_timeout = list(download_codes), submit_timeout
        self.submits: list[dict] = []
        self.paths: list[str] = []
        self.uploads: list[str] = []

    @staticmethod
    def _next(seq: list):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if req.method == "POST":
            self.submits.append(json.loads(req.content))
            self.paths.append(req.url.path)
            if self.submit_timeout:
                raise httpx.ReadTimeout("no response", request=req)
            code = self._next(self.submit_codes)
            if code != 200:
                return httpx.Response(code, json=self.error_body)
            return httpx.Response(200, json={"request_id": "r1", "status_url": "https://q/s",
                                             "response_url": "https://q/r"})  # fmt: skip
        if u == "https://q/s":
            st = self._next(self.statuses)
            return (
                httpx.Response(st, json={})
                if isinstance(st, int)
                else httpx.Response(200, json={"status": st})
            )
        if u == "https://q/r":
            code = self._next(self.result_codes)
            return httpx.Response(code, json=self.result if code == 200 else self.error_body)
        code = self._next(self.download_codes)
        return httpx.Response(code, content=b"FILE" if code == 200 else b"")


def queue(mock) -> fal.FalQueue:
    q = fal.FalQueue(
        settings(fal_key="test"), http=httpx.Client(transport=httpx.MockTransport(mock)), poll_s=0
    )

    def upload(path):
        mock.uploads.append(Path(path).name)
        return f"https://cdn/up/{Path(path).name}"

    q.upload = upload  # official fal_client, no network in tests
    return q


def video(mock):
    return fal.FalVideo(registry.get("video", "kling-v3-std"), queue(mock))


def frame(tmp_path, name="s1.png"):
    img = tmp_path / name
    img.write_bytes(b"PNG")
    return img


def test_kling_shot1_from_text_shot2_from_cut_frame_both_silent(tmp_path):
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}})
    video(mock).animate(None, "scene", 5.0, tmp_path / "s1.mp4")
    img = frame(tmp_path, "s2.png")
    video(mock).animate(img, "she turns", 5.0, tmp_path / "s2.mp4")
    assert mock.paths == [
        "/fal-ai/kling-video/v3/standard/text-to-video",
        "/fal-ai/kling-video/v3/standard/image-to-video",
    ]
    t2v, i2v = mock.submits
    assert t2v["aspect_ratio"] == "9:16" and t2v["generate_audio"] is False
    assert "start_image_url" not in t2v and t2v["duration"] == "5"
    assert i2v["start_image_url"] == "https://cdn/up/s2.png"  # official upload, not a data URI
    assert i2v["generate_audio"] is False and i2v["duration"] == "5"
    video(mock).animate(img, "she turns again", 5.0, tmp_path / "s2b.mp4")
    assert mock.uploads == ["s2.png"]  # the same file: URL from the sidecar
    assert fal.known_url(tmp_path / "s1.mp4") == "https://cdn/v.mp4"


def test_new_take_is_a_new_job_not_the_saved_result(tmp_path):
    # Kling takes no seed: without the take in the key a regeneration would return the saved result.
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}})
    out = tmp_path / "s1.mp4"
    video(mock).animate(None, "scene", 5.0, out, seed=0)
    video(mock).animate(None, "scene", 5.0, out, seed=0)  # same take: from disk
    assert len(mock.submits) == 1
    video(mock).animate(None, "scene", 5.0, out, seed=1000)  # new take: a new job
    assert len(mock.submits) == 2


def test_changed_file_is_uploaded_again(tmp_path):
    img = frame(tmp_path)
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}})
    video(mock).animate(img, "m", 3.0, tmp_path / "a.mp4")
    img.write_bytes(b"PNG-NEW")  # e.g. a new cut frame after a new take of the shot
    video(mock).animate(img, "m2", 3.0, tmp_path / "b.mp4")
    assert mock.uploads == ["s1.png", "s1.png"]


@pytest.mark.parametrize("where", ["status", "result"])
def test_transient_5xx_while_waiting_never_resubmits(tmp_path, where):
    kw = {"statuses": [503, "IN_PROGRESS", "COMPLETED"]} if where == "status" else {
        "result_codes": [502, 200]}  # fmt: skip
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}}, **kw)
    video(mock).animate(frame(tmp_path), "m", 3.0, tmp_path / "s1.mp4")
    assert len(mock.submits) == 1  # before: a 5xx while polling created a new paid job


def test_deadline_keeps_job_and_resume_does_not_pay_twice(tmp_path, monkeypatch):
    t = iter(range(0, 10**6, 100))
    monkeypatch.setattr(fal.time, "monotonic", lambda: next(t))
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}}, statuses=["IN_QUEUE"])
    out = tmp_path / "s1.mp4"
    with pytest.raises(fal.FalStillRunning):
        video(mock).animate(frame(tmp_path), "m", 3.0, out)
    assert len(mock.submits) == 1 and out.with_name("s1.mp4.fal-job.json").exists()
    mock.statuses = ["COMPLETED"]  # the job finished while we were away
    video(mock).animate(frame(tmp_path), "m", 3.0, out)
    assert len(mock.submits) == 1 and out.read_bytes() == b"FILE"
    assert not out.with_name("s1.mp4.fal-job.json").exists()


def test_download_failure_then_resume_uses_saved_result(tmp_path):
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}}, download_codes=[500])
    out = tmp_path / "s1.mp4"
    with pytest.raises(httpx.HTTPStatusError):
        video(mock).animate(frame(tmp_path), "m", 3.0, out)
    assert out.with_name("s1.mp4.fal-result.json").exists()
    mock.download_codes = [200]
    video(mock).animate(frame(tmp_path), "m", 3.0, out)
    assert len(mock.submits) == 1 and out.read_bytes() == b"FILE"


def test_submit_rejected_is_not_billed_and_not_retried(tmp_path):
    mock = FalMock({}, submit=[422])
    with pytest.raises(fal.FalRejected) as e:
        video(mock).animate(frame(tmp_path), "m", 3.0, tmp_path / "s1.mp4")
    assert len(mock.submits) == 1
    from pipeline.billing import not_billed

    assert not_billed(e.value)


REFUSED = {"detail": [{"type": "content_policy_violation", "loc": ["body"],
                       "msg": "flagged by a content checker"}]}  # fmt: skip


def test_content_refusal_is_named_not_a_raw_422(tmp_path):
    # Regression (run 2026-10-02-02): Kling refused a harmless prompt; the job was COMPLETED and
    # its result a 422. The UI showed a raw HTTPStatusError and offered a resume that only reads
    # the same 422 again.
    from pipeline.providers import ContentRejected

    mock = FalMock({}, statuses=["COMPLETED"], result_codes=[422], error_body=REFUSED)
    with pytest.raises(ContentRejected, match="content checker refused") as e:
        video(mock).animate(None, "m", 3.0, tmp_path / "s1.mp4")
    assert e.value.content_rejected and len(mock.submits) == 1
    from pipeline.billing import not_billed

    assert not not_billed(e.value)  # fal: a 422 "may still be charged" -> the reserve stays


def test_content_refusal_on_submit_is_not_billed(tmp_path):
    mock = FalMock({}, submit=[422], error_body=REFUSED)
    with pytest.raises(fal.FalRejected) as e:
        video(mock).animate(None, "m", 3.0, tmp_path / "s1.mp4")
    from pipeline.billing import not_billed

    assert e.value.content_rejected and not_billed(e.value)


def test_other_422_on_result_stays_an_http_error(tmp_path):
    mock = FalMock({}, statuses=["COMPLETED"], result_codes=[422])
    with pytest.raises(httpx.HTTPStatusError):
        video(mock).animate(None, "m", 3.0, tmp_path / "s1.mp4")


def test_submit_5xx_is_retried(tmp_path):
    mock = FalMock({"video": {"url": "https://cdn/v.mp4"}}, submit=[500, 200])
    video(mock).animate(frame(tmp_path), "m", 3.0, tmp_path / "s1.mp4")
    assert len(mock.submits) == 2  # the server refused, no job was created, a retry is safe


def test_submit_read_timeout_is_not_retried(tmp_path):
    mock = FalMock({}, submit_timeout=True)
    with pytest.raises(httpx.ReadTimeout):
        video(mock).animate(frame(tmp_path), "m", 3.0, tmp_path / "s1.mp4")
    assert len(mock.submits) == 1  # fal may have accepted the job: a retry would pay twice


def test_failed_status_raises(tmp_path):
    mock = FalMock({}, statuses=["FAILED"])
    with pytest.raises(fal.FalFailed):
        video(mock).animate(frame(tmp_path), "m", 3.0, tmp_path / "s1.mp4")
    assert len(mock.submits) == 1


def test_fal_without_key_is_config_error_only_on_use(tmp_path):
    q = fal.FalQueue(settings())
    with pytest.raises(ConfigError):
        q.run("x", {}, timeout_s=1, job_file=tmp_path / "j.json")


# ---------- lipsync ----------


def test_lipsync_uploads_our_audio_and_uses_clip_url(tmp_path):
    from pipeline.media import run_ffmpeg

    clip = tmp_path / "s1.mp4"
    clip.write_bytes(b"MP4")
    fal.remember_url(clip, "https://cdn/clip.mp4")
    wav = tmp_path / "s1.lip.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=300:d=1", str(wav)])
    mock = FalMock({"video": {"url": "https://cdn/synced.mp4"}})
    ls = fal.FalLipSync(registry.get("lipsync", "sync-lipsync-v2"), queue(mock))
    ls.sync(clip, wav, tmp_path / "s1.lipsync.mp4")
    sub = mock.submits[0]
    assert sub["video_url"] == "https://cdn/clip.mp4"
    assert sub["audio_url"] == "https://cdn/up/s1.lip.upload.mp3" and sub["sync_mode"] == "cut_off"
    assert not (tmp_path / "s1.lip.upload.mp3").exists()


# ---------- ASR over the API ----------


def test_fal_whisper_text_and_speech_window(tmp_path):
    from pipeline.media import run_ffmpeg

    wav = tmp_path / "s1.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=300:d=1", str(wav)])
    result = {
        "text": " I never wrote this. ",
        "chunks": [
            {"timestamp": [0.42, 0.6], "text": "I"},
            {"timestamp": [0.6, 0.9], "text": "never"},
            {"timestamp": [1.8, 2.05], "text": "this."},
        ],
    }
    mock = FalMock(result)
    asr = fal.FalASR(registry.get("asr", "fal-whisper"), queue(mock))
    t = asr.timed(wav)
    assert t.text == "I never wrote this." and (t.speech_start_s, t.speech_end_s) == (0.42, 2.05)
    sub = mock.submits[0]
    assert sub["chunk_level"] == "word" and sub["language"] == "en"
    assert sub["audio_url"] == "https://cdn/up/s1.upload.mp3"
    assert asr.name == "fal-whisper"  # independent of the TTS synthesizer
