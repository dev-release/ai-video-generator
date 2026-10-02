"""Settings and pipeline constants. Model prices, keys and limits live in models.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# Paid calls allowed per unit (one script take, one clip, one wav): a retry loop on a single
# artifact is caught whatever the number of shots.
MAX_CALLS_PER_UNIT: dict[str, int] = {
    "script": 3,  # 1 + MAX_SCRIPT_FIX_ATTEMPTS
    "voice": 3,  # 1 + MAX_VOICE_ATTEMPTS
    "video": 3,  # 1 + two manual retakes
    "lipsync": 3,
}
MAX_TTS_CHARS = 600  # fallback when the model entry has no max_chars
MAX_CLIP_S = 15.0

MAX_FINAL_S = 30.0
TARGET_LUFS = -14.0
MAX_VOICE_ATTEMPTS = 2
# A retake shifts the seed by this stride so it never collides with gate retries.
TAKE_STRIDE = 1000
# Silence padded around the copy sent to the final ASR: Whisper drops the first word when speech
# starts within ~0.4 s of the file start. The video itself is not changed.
ASR_PAD_S = 0.5
# Rounding of the speech window in the video prompt, so a new voice take does not change the
# prompt (and re-bill the video) over milliseconds.
SPEECH_WINDOW_STEP_S = 0.5

# Lip sync check (spec sync.md). Calibrated on few real clips — provisional.
SYNC_FPS = 30
SYNC_MAX_LAG_MS = 400
SYNC_OK_LAG_MS = 160  # real synced clip: 133 ms; clips shifted by 0.3–0.5 s: 300–400 ms
SYNC_MIN_CORR = 0.3
SYNC_MIN_MOUTH_RATIO = 1.3
SYNC_MIN_FACE_RATIO = 0.5
SYNC_MAX_YAW = 0.5  # head turn from YuNet points: frontal ≈0.1, 3/4 ≈0.25–0.42, more is profile
MAX_SCRIPT_FIX_ATTEMPTS = 2


class Pacing(BaseModel):
    """Pauses and durations (spec assemble.md). A shot is lead + line (with pauses between
    phrases) + tail, at least min_shot. min_final > 0 pads the video with a freeze frame."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lead_s: float = Field(0.3, ge=0, le=3)
    tail_s: float = Field(0.3, ge=0, le=3)
    min_shot_s: float = Field(2.0, ge=0, le=10)
    min_final_s: float = Field(0.0, ge=0, le=30)
    # The line is split after these marks (when text follows) and silence is inserted; a comma
    # is left to the TTS.
    pause_sentence_s: float = Field(0.6, ge=0, le=5)  # . ! ?
    pause_ellipsis_s: float = Field(1.0, ge=0, le=5)  # ...
    pause_dash_s: float = Field(0.5, ge=0, le=5)  # —
    pause_colon_s: float = Field(0.4, ge=0, le=5)  # ; :

    def pause_after(self, mark: str) -> float:
        return {"...": self.pause_ellipsis_s, "—": self.pause_dash_s, ";": self.pause_colon_s,
                ":": self.pause_colon_s}.get(mark, self.pause_sentence_s)  # fmt: skip


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    pipeline_providers: Literal["fake", "real"] = "real"
    # The caps are hard upper bounds; guard-bash.sh also blocks raising them from the shell.
    max_run_cost_usd: float = Field(default=1.0, gt=0, le=5.0)
    max_daily_cost_usd: float = Field(default=3.0, gt=0, le=20.0)  # all runs, UTC day
    sync_gate: Literal["warn", "block"] = "warn"
    runs_dir: Path = ROOT / "runs"
    cache_dir: Path | None = None  # shared cache of paid artifacts; None -> runs_dir/_cache
    auto_approve: bool = False

    # Real-mode models: entry names in models.yaml.
    script_model: str = "claude-opus-5-5"
    tts_model: str = "kokoro"
    video_model: str = "kling-v3-std"
    lipsync_model: str = "sync-lipsync-v2"
    asr_model: str = "fal-whisper"
    face_model: str = "yunet"
    # Fake mode voice: tone needs no models (tests, CI); kokoro needs `make install-local`.
    fake_voice: Literal["kokoro", "tone"] = "tone"

    pace_lead_s: float = Pacing().lead_s
    pace_tail_s: float = Pacing().tail_s
    pace_min_shot_s: float = Pacing().min_shot_s
    pace_min_final_s: float = Pacing().min_final_s
    pause_sentence_s: float = Pacing().pause_sentence_s
    pause_ellipsis_s: float = Pacing().pause_ellipsis_s
    pause_dash_s: float = Pacing().pause_dash_s
    pause_colon_s: float = Pacing().pause_colon_s

    anthropic_api_key: str = ""
    fal_key: str = ""

    def pacing(self) -> Pacing:
        return Pacing(
            lead_s=self.pace_lead_s, tail_s=self.pace_tail_s, min_shot_s=self.pace_min_shot_s,
            min_final_s=self.pace_min_final_s, pause_sentence_s=self.pause_sentence_s,
            pause_ellipsis_s=self.pause_ellipsis_s, pause_dash_s=self.pause_dash_s,
            pause_colon_s=self.pause_colon_s,
        )  # fmt: skip

    def cache_root(self) -> Path:
        return self.cache_dir or self.runs_dir / "_cache"

    def selected(self) -> dict[str, str]:
        """Selected model per capability (registry, run's models.json, eval reports)."""
        if self.pipeline_providers == "fake":
            voice = self.fake_voice
            return {
                "script": "fake",
                "tts": voice,
                "video": "fake",
                "lipsync": "fake",
                "asr": "faster-whisper" if voice == "kokoro" else "echo",
                "face": "fake",
            }
        return {
            "script": self.script_model,
            "tts": self.tts_model,
            "video": self.video_model,
            "lipsync": self.lipsync_model,
            "asr": self.asr_model,
            "face": self.face_model,
        }


@lru_cache
def settings() -> Settings:
    return Settings()
