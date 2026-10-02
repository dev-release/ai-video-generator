"""fal.ai: one transport for every fal model — video, lipsync, ASR (spec: providers.md).

What to call and how to read the response is the models.yaml entry (endpoint, args template,
output path). No per-model classes: replacing Kling with another fal model is yaml only.

Money (spec pipeline.md, "Paid calls"): a job is submitted EXACTLY ONCE.
- submit: `submit_retry` (5xx / not delivered only); `<out>.fal-job.json` is written right away;
- status polling and the result: `read_retry` per GET, never a re-submit;
- wait deadline -> `FalStillRunning` (not a retry): the job lives on and a resume picks it up;
- the result is saved to `<out>.fal-result.json` before the file download, so a failed download
  never means paying again.
Queue: https://fal.ai/docs/model-endpoints/queue (2026-09-29). Files between steps are fal CDN
URLs (official `fal_client.upload_file`) with a `<file>.url` sidecar holding the file hash: a
changed file is uploaded again instead of reusing a stale URL.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from pipeline import cache
from pipeline.config import Settings
from pipeline.media import run_ffmpeg, write_atomic
from pipeline.providers import ContentRejected, SpecProvider, Timed
from pipeline.registry import ModelSpec, extract, render
from pipeline.retry import read_retry, submit_retry
from pipeline.voices import ConfigError

QUEUE = "https://queue.fal.run"


class FalStillRunning(RuntimeError):
    """The fal job still runs after the wait deadline. Not retried: re-submitting means paying
    again. Resuming the run picks up the same job from `<out>.fal-job.json`."""

    still_running = True


class FalRejected(RuntimeError):
    """fal rejected the job on submit (4xx): not run, not billed."""

    billed = False


class FalRefusedOnSubmit(FalRejected, ContentRejected):
    """The content checker refused the job on submit: not run, not billed."""


class FalFailed(RuntimeError):
    pass


class FalQueue:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, poll_s: float = 2.0):
        self.settings = settings
        self._http = http
        self.poll_s = poll_s

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            if not self.settings.fal_key:
                raise ConfigError("FAL_KEY is not set — see .env.example")
            auth = {"Authorization": f"Key {self.settings.fal_key}"}
            self._http = httpx.Client(headers=auth, timeout=60)
        return self._http

    @submit_retry
    def _submit(self, endpoint: str, args: dict) -> dict:
        r = self.http.post(f"{QUEUE}/{endpoint}", json=args)
        if 400 <= r.status_code < 500:
            err = FalRefusedOnSubmit if content_policy(r) else FalRejected
            raise err(f"{endpoint}: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
        return r.json()

    @read_retry
    def _get(self, url: str) -> httpx.Response:
        r = self.http.get(url)
        r.raise_for_status()
        return r

    def run(
        self, endpoint: str, args: dict, timeout_s: float, job_file: Path, take: int = 0
    ) -> dict:
        """One job = one payment. An unfinished job in `job_file` is picked up, not re-sent.
        take is the take number: the same request in a new take is a new job."""
        args_hash = cache.input_hash(endpoint, args, take)
        job = json.loads(job_file.read_text()) if job_file.exists() else None
        if not job or job.get("args_hash") != args_hash:
            job = {**self._submit(endpoint, args), "endpoint": endpoint, "args_hash": args_hash}
            write_atomic(job_file, json.dumps(job, indent=2))
        deadline = time.monotonic() + timeout_s
        while True:
            s = self._get(job["status_url"])
            status = s.json().get("status")
            if status == "COMPLETED":
                break
            if status not in ("IN_QUEUE", "IN_PROGRESS"):
                raise FalFailed(f"{endpoint}: status {status}: {s.text[:300]}")
            if time.monotonic() > deadline:
                raise FalStillRunning(
                    f"{endpoint}: job {job.get('request_id')} still running after "
                    f"{timeout_s:.0f} s. Nothing was re-submitted — resume the run later "
                    "(CLI: --run-id, UI: Continue) and it will pick up this same job."
                )
            time.sleep(self.poll_s)
        try:
            return self._get(job["response_url"]).json()
        except httpx.HTTPStatusError as e:
            # A refused job ends as COMPLETED with a 422 result: name it, so the UI offers a new
            # take or a rewrite instead of a resume that would only read the same 422 again.
            if reason := content_policy(e.response):
                raise ContentRejected(
                    f"{endpoint}: the model's content checker refused the request ({reason}). "
                    "Resuming repeats the same request: make a new take or rewrite the script."
                ) from e
            raise

    @read_retry
    def download(self, url: str, out: Path) -> Path:
        r = self.http.get(url, follow_redirects=True)
        r.raise_for_status()
        write_atomic(out, r.content)
        return out

    @read_retry
    def upload(self, path: Path) -> str:
        """Official upload to the fal CDN. A retry is safe: only a new URL, no charge."""
        import fal_client

        return fal_client.SyncClient(key=self.settings.fal_key).upload_file(path)


def content_policy(r: httpx.Response) -> str | None:
    """fal error body: {"detail": [{"type": "content_policy_violation", "msg": …}]}
    (https://fal.ai/docs/errors, 2026-10-02)."""
    if r.status_code != 422:
        return None
    try:
        detail = r.json().get("detail")
    except ValueError:
        return None
    for d in detail if isinstance(detail, list) else []:
        if isinstance(d, dict) and d.get("type") == "content_policy_violation":
            return str(d.get("msg") or "content_policy_violation")
    return None


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".url")


def remember_url(path: Path, url: str) -> None:
    write_atomic(_sidecar(path), json.dumps({"url": url, "sha": cache.file_hash(path)}))


def known_url(path: Path) -> str | None:
    """The file's fal URL, only if the file has not changed since it was uploaded/generated."""
    side = _sidecar(path)
    if not side.exists():
        return None
    try:
        rec = json.loads(side.read_text())
    except json.JSONDecodeError:
        return None
    return rec["url"] if rec.get("sha") == cache.file_hash(path) else None


class FalAdapter(SpecProvider):
    def __init__(self, spec: ModelSpec, queue: FalQueue) -> None:
        self.spec, self.q = spec, queue

    def ready(self) -> None:
        _ = self.q.http

    def _url(self, path: Path) -> str:
        if url := known_url(path):
            return url
        url = self.q.upload(path)
        remember_url(path, url)
        return url

    def _audio_url(self, wav: Path) -> str:
        # mp3 64 kbit/s is 10x smaller than wav; the URL is bound to the wav hash.
        if url := known_url(wav):
            return url
        mp3 = wav.with_suffix(".upload.mp3")
        run_ffmpeg(["-i", str(wav), "-ac", "1", "-b:a", "64k", str(mp3)])
        try:
            url = self.q.upload(mp3)
        finally:
            mp3.unlink(missing_ok=True)
        remember_url(wav, url)
        return url

    def _result(self, endpoint: str, template: dict, out: Path, take: int = 0, **vars) -> dict:
        """Job result: from disk if already fetched for the same args and take, else from fal.
        Without the take in the key a retake of a seedless model (Kling) returns the old one."""
        args = render(template, **vars)
        res_file = out.with_name(out.name + ".fal-result.json")
        args_hash = cache.input_hash(endpoint, args, take)
        if res_file.exists():
            saved = json.loads(res_file.read_text())
            if saved.get("args_hash") == args_hash:
                return saved["result"]
        job_file = out.with_name(out.name + ".fal-job.json")
        res = self.q.run(endpoint, args, self.spec.timeout_s, job_file, take)
        write_atomic(res_file, json.dumps({"args_hash": args_hash, "result": res}))
        job_file.unlink(missing_ok=True)
        return res

    def _save(self, res: dict, out: Path) -> Path:
        url = extract(res, self.spec.output)
        self.q.download(url, out)
        remember_url(out, url)  # the next step uses the URL, no re-upload
        return out


class FalVideo(FalAdapter):
    def animate(
        self, image: Path | None, motion: str, duration_s: float, out: Path, seed: int = 0
    ) -> Path:
        # Kling on fal takes no seed: a new take is a new request (another job key on disk).
        s, d = self.spec, int(round(duration_s))
        vars = dict(prompt=motion, duration_int=d, duration_str=str(d))
        if image is None:  # shot 1, from text
            res = self._result(s.endpoint, s.args, out, take=seed, **vars)
        else:  # shot k, from the cut frame of shot k-1
            assert s.reference_endpoint, f"{s.name}: no image-to-video endpoint"
            res = self._result(s.reference_endpoint, s.reference_args, out, take=seed,
                               image_url=self._url(image), **vars)  # fmt: skip
        return self._save(res, out)


class FalLipSync(FalAdapter):
    """Lips to OUR audio. assemble drops the audio of the result (spec sync.md)."""

    def sync(self, clip: Path, shot_audio: Path, out: Path, seed: int = 0) -> Path:
        urls = dict(video_url=self._url(clip), audio_url=self._audio_url(shot_audio))
        res = self._result(self.spec.endpoint, self.spec.args, out, take=seed, **urls)
        return self._save(res, out)


class FalASR(FalAdapter):
    """ASR over the API: independent of the TTS provider, no local models."""

    def _run(self, wav: Path) -> dict:
        # Free (price 0), but jobs are still persisted so a retry does not send another one.
        out = wav.with_name(wav.name + ".asr")
        return self._result(self.spec.endpoint, self.spec.args, out, audio_url=self._audio_url(wav))

    def transcribe(self, wav: Path) -> str:
        return str(extract(self._run(wav), self.spec.output)).strip()

    def timed(self, wav: Path) -> Timed:
        res = self._run(wav)
        text = str(extract(res, self.spec.output)).strip()
        w = self.spec.words
        items = extract(res, w.path) if w else []
        if not items:
            return Timed(text, 0.0, 0.0)
        return Timed(text, float(extract(items[0], w.start)), float(extract(items[-1], w.end)))
