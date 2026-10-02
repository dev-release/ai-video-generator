"""Contract tests: the SAME rules for every model in models.yaml.

A new model is a registry entry and automatically goes through everything below.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pipeline import pricing, registry
from pipeline.config import MAX_CLIP_S, Settings
from pipeline.providers import Deps, build, make
from pipeline.providers import fal as fal_mod
from pipeline.schema import Brief

ALL = sorted(registry.catalog().values(), key=lambda s: (s.capability, s.name))
NETWORK = [s for s in ALL if s.transport in registry.NETWORK]
FAL = [s for s in ALL if s.transport == "fal"]
VARS = {
    "video": {"image_url": "https://i", "prompt": "p", "duration_int": 5, "duration_str": "5"},
    "lipsync": {"video_url": "https://v", "audio_url": "data:audio/mpeg;base64,AA"},
    "asr": {"audio_url": "data:audio/mpeg;base64,AA"},
}
ids = [f"{s.capability}.{s.name}" for s in ALL]


def _nest(path: str, value) -> dict:
    """A sample response with a field at the output path (video.url -> {"video":{"url":…}})."""
    obj: object = value
    for part in reversed(path.split(".")):
        obj = [obj] if part.isdigit() else {part: obj}
    return obj  # type: ignore[return-value]


@pytest.mark.parametrize("spec", NETWORK, ids=[f"{s.capability}.{s.name}" for s in NETWORK])
def test_network_model_declares_price_source_key_timeout(spec):
    assert spec.price.source and spec.price.checked
    assert spec.key and spec.key.isupper() and spec.endpoint
    assert spec.timeout_s > 0
    assert hasattr(Settings(_env_file=None), spec.key.lower()), "the key must be a Settings field"


@pytest.mark.parametrize("spec", FAL, ids=[f"{s.capability}.{s.name}" for s in FAL])
def test_fal_template_renders_and_output_extracts(spec):
    args = registry.render(spec.args, **VARS[spec.capability])
    assert not registry._VAR.search(json.dumps(args)), "every template variable is filled"
    assert registry.extract(_nest(spec.output, "X"), spec.output) == "X"
    if spec.reference_endpoint:  # reference_args is a full template (spec providers.md)
        ref = registry.render(spec.reference_args, **VARS[spec.capability])
        assert not registry._VAR.search(json.dumps(ref))


@pytest.mark.parametrize("spec", [s for s in ALL if s.capability == "video"], ids=lambda s: s.name)
def test_video_is_silent_and_durations_bounded(spec):
    assert spec.durations_s == sorted(spec.durations_s)
    assert max(spec.durations_s) <= MAX_CLIP_S
    if spec.transport == "fal":
        # Shot 1 from text at 9:16, shot k from the cut frame; audio off in both (spec video.md).
        assert spec.args.get("aspect_ratio") == "9:16" and spec.reference_endpoint
        assert spec.reference_args.get("start_image_url") == "{image_url}"
        for tpl in (spec.args, spec.reference_args):
            assert all(tpl.get(k) == v for k, v in spec.audio_off.items())
            # Banned things go to the negative_prompt, not "no text" in the prompt (spec video.md).
            neg = tpl.get("negative_prompt", "")
            assert all(w in neg for w in ("text", "subtitles", "watermark", "deformed"))
    if spec.native_audio:
        assert spec.audio_off and all(spec.args[k] == v for k, v in spec.audio_off.items())


@pytest.mark.parametrize("spec", ALL, ids=ids)
def test_every_model_has_an_adapter(spec):
    if spec.transport == "local" and spec.capability in ("tts", "asr"):
        pytest.importorskip("kokoro_onnx")  # optional extra `local`
    prov = make(spec, Deps(Settings(_env_file=None)))
    assert prov.name == spec.name and prov.spec is spec


@pytest.mark.parametrize("spec", ALL, ids=ids)
def test_every_model_is_priced(spec):
    assert pricing.cost(spec, calls=1, seconds=5, chars=300) >= 0


def test_default_real_models_exist():
    s = Settings(_env_file=None, pipeline_providers="real")
    for cap, name in s.selected().items():
        assert registry.get(cap, name).capability == cap


def test_switching_video_model_is_config_only():
    s = Settings(_env_file=None, pipeline_providers="real", video_model="kling-v3-pro")
    p = build(s)
    assert p.video.name == "kling-v3-pro"
    assert p.video.spec.endpoint.endswith("/pro/text-to-video")
    assert (p.video.spec.reference_endpoint or "").endswith("/pro/image-to-video")
    assert type(p.video) is type(build(Settings(_env_file=None, pipeline_providers="real")).video)


def test_nodes_do_not_branch_on_model_name():
    # Nodes know the capability protocol, not the model. Regression: `face.name == "no-face"`
    # in sync_check was a name branch that never even fired.
    import re

    src = Path(registry.__file__).parent
    files = [*sorted((src / "nodes").glob("*.py")), src / "graph.py", src / "timeline.py"]
    hits = [
        f"{f.name}:{i}: {line.strip()}"
        for f in files
        for i, line in enumerate(f.read_text().splitlines(), 1)
        if re.search(r"\.name\s*(==|!=|in\b)", line)
    ]
    assert not hits, "branching on a model name:\n" + "\n".join(hits)


class QueueMock:
    def __init__(self, result: dict):
        self.result, self.submits = result, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            self.submits.append((req.url.path, json.loads(req.content)))
            return httpx.Response(
                200, json={"status_url": "https://q/s", "response_url": "https://q/r"}
            )
        if str(req.url) == "https://q/s":
            return httpx.Response(200, json={"status": "COMPLETED"})
        if str(req.url) == "https://q/r":
            return httpx.Response(200, json=self.result)
        return httpx.Response(200, content=b"FILE")


@pytest.mark.parametrize("spec", FAL, ids=[f"{s.capability}.{s.name}" for s in FAL])
def test_fal_adapter_sends_rendered_args_and_saves_output(spec, tmp_path, monkeypatch):
    """One adapter per capability, the same behavior for any fal model: one submit, input
    files as URLs (not data URIs), the result saved together with its URL."""
    from pipeline.media import run_ffmpeg

    monkeypatch.setattr("time.sleep", lambda _s: None)
    mock = QueueMock(_nest(spec.output, "https://cdn/out" if spec.capability != "asr" else "hi"))
    s = Settings(_env_file=None, fal_key="test")
    q = fal_mod.FalQueue(s, http=httpx.Client(transport=httpx.MockTransport(mock)), poll_s=0)
    q.upload = lambda path: f"https://cdn/up/{Path(path).name}"
    prov = make(spec, Deps(s, _fal=q))
    img = tmp_path / "in.png"
    img.write_bytes(b"PNG")
    fal_mod.remember_url(img, "https://i")
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"MP4")
    fal_mod.remember_url(clip, "https://v")
    wav = tmp_path / "a.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=300:d=1", str(wav)])
    out = tmp_path / "out.bin"
    match spec.capability:
        case "video":
            prov.animate(None, "p", 5.0, out)  # shot 1, from text (endpoint)
        case "lipsync":
            prov.sync(clip, wav, out)
        case "asr":
            assert prov.transcribe(wav) == "hi"
    assert len(mock.submits) == 1
    path, sent = mock.submits[0]
    assert path == f"/{spec.endpoint}"
    assert "data:" not in json.dumps(sent), "files go as URLs, not data URIs"
    for k, v in spec.args.items():
        if not (isinstance(v, str) and "{" in v):
            assert sent[k] == v, f"fixed model args are passed as is: {k}"
    if spec.capability != "asr":
        assert out.read_bytes() == b"FILE" and fal_mod.known_url(out) == "https://cdn/out"


def test_estimate_follows_selected_models():
    from pipeline.preflight import estimate

    brief = Brief.model_validate({"idea": 'x x "I never wrote this letter at all."'})
    std = estimate(brief, build(Settings(_env_file=None, pipeline_providers="real")))[0]
    pro = estimate(
        brief,
        build(Settings(_env_file=None, pipeline_providers="real", video_model="kling-v3-pro")),
    )[0]
    assert pro > std  # the price comes from the model entry, no per-model code
