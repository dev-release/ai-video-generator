"""Casting + TTS from the exact Shot.line. Spec: .claude/specs/voice.md."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import NamedTuple

import numpy as np

from pipeline import cache, pricing, runmemo, voices
from pipeline.config import MAX_TTS_CHARS, Pacing
from pipeline.media import probe, read_pcm, write_atomic, write_wav
from pipeline.nodes import Ctx, produce, run_path
from pipeline.schema import CastEntry, PipelineState, Shot, VoiceClip

SR = 44100
# A pause mark followed by text: ellipsis, sentence end, ; or :, a dash with or without spaces.
_BREAK = re.compile(r"(\.{2,}|…|[.!?;:]+)(?=\s+\S)|\s+[—–-]\s+(?=\S)|[—–](?=\S)")
_ABBR = re.compile(r"\b(?:mr|mrs|ms|dr|st|jr|sr|vs|prof)\.$", re.IGNORECASE)


class Phrase(NamedTuple):
    text: str  # substring of the line, verbatim
    pause_after_s: float


def _pause(mark: str, p: Pacing) -> float:
    m = mark.strip()
    return p.pause_after("..." if m.startswith(("..", "…")) else "—" if m in "—–-" else m[-1])


def phrases(line: str, p: Pacing | None = None) -> list[Phrase]:
    """Phrases of a line for pauses. Works on a COPY: Shot.line is never changed."""
    out, start = [], 0
    for m in _BREAK.finditer(line):
        text = line[start : m.end()].strip()
        if _ABBR.search(line[: m.end()]) or not text.strip("—–- "):
            continue  # "Dr. Smith" is not a pause; a leading dash is not a phrase
        out.append(Phrase(text, _pause(m.group(), p or Pacing())))
        start = m.end()
    return [*out, Phrase(line[start:].strip(), 0.0)]


def _trim(x: np.ndarray, head: bool, tail: bool) -> np.ndarray:
    """Drop TTS silence at phrase edges (-40 dB from peak, 40 ms margin): pauses are ours."""
    loud = np.flatnonzero(np.abs(x) > np.abs(x).max(initial=0) * 0.01)
    if not len(loud):
        return x
    pad = int(0.04 * SR)
    return x[max(0, loud[0] - pad) if head else 0 : loud[-1] + pad + 1 if tail else len(x)]


def join_phrases(parts: list[Path], pauses: list[float], out: Path) -> list[tuple[float, float]]:
    """Phrases + silence between them -> one shot wav. Returns the pause spans in it (s)."""
    chunks, spans, t = [], [], 0.0
    for i, path in enumerate(parts):
        x = _trim(read_pcm(path, SR), head=i > 0, tail=i < len(parts) - 1)
        chunks.append(x)
        t += len(x) / SR
        if i < len(pauses):
            n = round(pauses[i] * SR)
            spans.append((round(t, 3), round(t + n / SR, 3)))
            chunks.append(np.zeros(n, dtype=np.float32))
            t += n / SR
    write_wav(out, np.concatenate(chunks), SR)
    return spans


def _casting(state: PipelineState, ctx: Ctx) -> dict[str, CastEntry]:
    path = run_path(state.run_dir, "casting.json")
    provider = ctx.providers.tts.spec.voice_catalog
    saved = (
        {k: CastEntry.model_validate(v) for k, v in json.loads(path.read_text()).items()}
        if path.exists()
        else {}
    )
    assert state.script
    # A character sounds the same in every run; a new character takes a free voice.
    casting = voices.cast(state.script.characters, provider, saved)
    if casting != saved:
        write_atomic(path, json.dumps({k: v.model_dump() for k, v in casting.items()}, indent=2))
    return casting


def _speak(ctx: Ctx, state: PipelineState, shot: Shot, text: str, voice: CastEntry, seed: int,
           out: Path, request: dict | None) -> None:  # fmt: skip
    """One paid TTS request through produce (cache, reserve, refund)."""
    tts = ctx.providers.tts
    limit = tts.spec.max_chars or MAX_TTS_CHARS
    if len(text) > limit:
        raise ValueError(f"{shot.id}: request of {len(text)} characters > model limit {limit}")
    key = cache.input_hash(text, voice.model_dump(), seed, tts.name, runmemo.salt(state.run_dir))
    req_path = out.with_suffix(".request.json")

    def make():
        tts.speak(text, voice, seed, out)
        if request is not None:
            write_atomic(req_path, json.dumps(request, ensure_ascii=False, indent=2))

    produce(ctx, node="voice", provider=tts, key=key, out=out, make=make,
            estimate_usd=pricing.cost(tts.spec, chars=len(text)), what=f"voice {out.stem}",
            sidecars=[*([req_path] if request is not None else []), out.with_suffix(".txt")],
            shot_id=shot.id)  # fmt: skip


def run(state: PipelineState, ctx: Ctx) -> dict:
    assert state.script
    casting = _casting(state, ctx)
    pacing = ctx.settings.pacing()
    clips: list[VoiceClip] = []
    for shot in state.script.shots:
        voice = casting[shot.character_id]
        # New seed only when the voice must differ: an audio gate retry or an explicit retake.
        seed = (
            voice.seed
            + state.voice_retries.get(shot.id, 0)
            + runmemo.seed_offset(state.run_dir, "voice", shot.id)
        )
        parts = phrases(shot.line, pacing)
        texts = [p.text for p in parts]
        out = run_path(state.run_dir, "voice", f"{shot.id}.wav")
        request = {
            "tts_text": "".join(
                f"{t} ⏸{p.pause_after_s:.1f}s " if p.pause_after_s else t
                for t, p in zip(texts, parts, strict=True)
            ),
            "line": shot.line,
            "parts": [p._asdict() for p in parts],
            "voice_id": voice.voice_id,
            "seed": seed,
        }
        pauses: list[tuple[float, float]] = []
        if len(parts) == 1:
            _speak(ctx, state, shot, texts[0], voice, seed, out, {**request, "pauses_s": []})
        else:
            files = [run_path(state.run_dir, "voice", "parts", f"{shot.id}.p{i}.wav")
                     for i in range(len(parts))]  # fmt: skip
            for text, f in zip(texts, files, strict=True):
                _speak(ctx, state, shot, text, voice, seed, f, None)
            # Joining is local and free; the key is the phrases and the pauses.
            key = cache.input_hash([cache.file_hash(f) for f in files], [p[1] for p in parts])
            req_path = out.with_suffix(".request.json")
            if cache.is_fresh(out, key) and req_path.exists():
                pauses = [tuple(x) for x in json.loads(req_path.read_text())["pauses_s"]]
            else:
                pauses = join_phrases(files, [p.pause_after_s for p in parts[:-1]], out)
                write_atomic(req_path, json.dumps({**request, "pauses_s": pauses},
                                                  ensure_ascii=False, indent=2))  # fmt: skip
                cache.mark(out, key)
        clips.append(
            VoiceClip(
                shot_id=shot.id,
                path=str(out),
                duration_s=probe(out).duration_s,
                text=shot.line,
                pauses_s=pauses,
            )  # fmt: skip
        )
    return {"casting": casting, "voices": clips}
