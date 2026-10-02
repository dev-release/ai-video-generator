"""Live smoke test of one model, the same for any registry entry (spec: providers.md).

    python -m pipeline.smoke script                          # estimate only; model from .env
    python -m pipeline.smoke video --model kling-v3-pro --yes
    python -m pipeline.smoke tts --voice-id am_puck --yes

For lipsync run video first. Every call goes through the Tracer (run cap, daily cap, call limit).
Without --yes only the estimate is printed. Artifacts: runs/_smoke/.
"""

from __future__ import annotations

import argparse
import time

from pipeline import pricing, registry
from pipeline.config import settings as load_settings
from pipeline.media import probe, run_ffmpeg, trim_audio
from pipeline.obs import Ledger, Tracer, console
from pipeline.providers import Deps, make
from pipeline.schema import Brief, CastEntry, VoiceProfile

BRIEF = Brief.model_validate({"idea": 'A girl finds a letter: "I never wrote this letter."'})
PROMPT = (
    "moody cinematic, warm window light. medium close-up, vertical 9:16. woman in her twenties. "
    "she slowly looks up from a letter. no text, no subtitles, no watermark, no logo."
)
NODE = {"script": "script", "tts": "voice", "video": "video", "lipsync": "lipsync",
        "asr": "verify"}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipeline.smoke")
    p.add_argument("capability", choices=list(NODE))
    p.add_argument("--model", help="entry name in models.yaml (default: from .env)")
    p.add_argument("--voice-id", help="tts: the voice to use")
    p.add_argument("--yes", action="store_true")
    a = p.parse_args(argv)
    s = load_settings()

    cap = a.capability
    spec = registry.get(
        cap, a.model or s.model_copy(update={"pipeline_providers": "real"}).selected()[cap]
    )
    out = s.runs_dir / "_smoke"
    out.mkdir(parents=True, exist_ok=True)
    est = pricing.cost(spec, seconds=3, chars=len(BRIEF.lines[0]) + 10)
    console.print(f"{cap} = {spec.name} ({spec.transport}): estimate ~${est:.3f}")
    if not a.yes:
        console.print("[yellow]nothing is called without --yes[/]")
        return 2

    # Validate inputs before reserving the cost, otherwise an error looks like spending.
    need = {"lipsync": out / "clip.mp4"}.get(cap)
    if cap == "tts" and not a.voice_id:
        console.print("[red]--voice-id is required[/]")
        return 2
    if need is not None and not need.exists():
        console.print("[red]run smoke video first[/]")
        return 2
    prov = make(spec, Deps(s))
    ledger = Ledger(s.runs_dir / "_ledger.jsonl")
    tracer = Tracer(out, s.max_run_cost_usd, s.max_daily_cost_usd, ledger)
    with tracer.spend(
        NODE[cap], prov, est, f"smoke {cap}={spec.name}", key=f"smoke:{cap}:{time.time()}"
    ):
        _call(cap, prov, a, out)
    return 0


def _call(cap: str, prov, a, out) -> None:
    if cap == "script":
        console.print_json(prov.write(BRIEF, None).model_dump_json())
    elif cap == "tts":
        voice = CastEntry(
            profile=VoiceProfile.female_young_warm, provider="smoke", voice_id=a.voice_id, seed=1
        )
        console.print(f"✓ {prov.speak(BRIEF.lines[0], voice, 1, out / 'voice.wav')}")
    elif cap == "video":
        console.print(f"✓ {prov.animate(None, PROMPT, 3, out / 'clip.mp4')}")
    elif cap == "lipsync":
        wav = out / "voice.wav"
        if not wav.exists():
            run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=3", str(wav)])
        clip = out / "clip.mp4"
        lip = trim_audio(wav, probe(clip).duration_s, out / "voice.lip.wav")
        console.print(f"✓ {prov.sync(clip, lip, out / 'clip.lipsync.mp4')}")
    else:
        console.print(f"✓ {prov.timed(out / 'voice.wav')}")


if __name__ == "__main__":
    raise SystemExit(main())
