import { useState } from 'react'
import { api, usd, type AsrCheck, type Decision, type RunView, type ShotSync } from './api'

type Act = (fn: () => Promise<unknown>) => void
type Shot = {
  id: string
  character_id: string
  line: string
  delivery: string
  visual_prompt: string
  camera: string
}
type Character = { id: string; name: string; voice_profile: string; look: string }
type Script = { title: string; style: string; characters: Character[]; shots: Shot[] }
type Casting = Record<string, { voice_id: string }>

// A character's voice comes from casting.json (the code picks it; the model gives only a profile).
function voiceOf(run: RunView, characterId: string): string | undefined {
  return (run.artifacts.casting as Casting | null)?.[characterId]?.voice_id
}

// Gate mismatches: the word that should have been heard and what whisper heard.
export function AsrDiff({ asr }: { asr: AsrCheck }) {
  const diff = asr.diff ?? []
  return (
    <div className="asr">
      <div className={`asr-verdict ${asr.passed ? 'ok' : 'err'}`}>
        {asr.passed ? '✓ Verbatim' : '✗ Not verbatim'} · WER {asr.wer} · attempt{' '}
        {asr.attempt + 1}
      </div>
      <div className="asr-row">
        <span className="muted">expected</span>
        <code>{asr.expected_norm}</code>
      </div>
      <div className="asr-row">
        <span className="muted">heard</span>
        <code>{asr.heard_norm || '(silence)'}</code>
      </div>
      {diff.length > 0 && (
        <ul className="diff">
          {diff.map((d, i) => (
            <li key={i}>
              <span className="tag">{d.op}</span> <del>{d.expected || '∅'}</del> →{' '}
              <ins>{d.heard || '∅'}</ins>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Two series on one chart: mouth openness (frames) and the loudness of our audio.
function SyncChart({ shot }: { shot: ShotSync }) {
  const W = 340
  const H = 70
  const n = Math.max(shot.env.length, shot.mouth.length, 2)
  // Frames without a face (null) break the line instead of dropping to zero.
  const path = (xs: (number | null)[]) => {
    let d = ''
    let pen = false
    xs.forEach((v, i) => {
      if (v == null) {
        pen = false
        return
      }
      d += `${pen ? 'L' : 'M'}${((i / (n - 1)) * W).toFixed(1)},${(H - v * H).toFixed(1)}`
      pen = true
    })
    return d
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="sync-chart" role="img" aria-label="mouth vs audio">
      <path d={path(shot.env)} className="env" />
      <path d={path(shot.mouth)} className="mouth" />
    </svg>
  )
}

function SyncPanel({ run }: { run: RunView }) {
  if (!run.sync) return <p className="muted">Not checked yet.</p>
  return (
    <div className="stack">
      <p className="muted small">
        Gate mode: <b>{run.sync.gate}</b>. <span className="legend env">■ our audio</span>{' '}
        <span className="legend mouth">■ mouth opening</span>
      </p>
      {run.sync.shots.map((s) => (
        <div key={s.shot_id} className="card">
          <div className="row between">
            <b>{s.shot_id}</b>
            <span className={`badge ${s.verdict === 'ok' ? 's-done' : s.verdict === 'off' ? 's-error' : ''}`}>
              {s.verdict === 'n/a' ? 'not measured (no face or turned away)' : s.verdict}
            </span>
          </div>
          {s.verdict !== 'n/a' && (
            <div className="small">
              lag {s.best_lag_ms} ms · correlation {s.corr} · mouth in speech / in silence {s.mouth_speech_ratio ?? '—'}
            </div>
          )}
          <div className="muted small">face in {Math.round(s.face_ratio * 100)}% of frames</div>
          <SyncChart shot={s} />
        </div>
      ))}
    </div>
  )
}

// End of the previous shot's video: the player sits on the cut second (at_s) to compare with the frame.
function ClipAt({ url, at }: { url: string; at: number }) {
  return (
    <video
      src={url}
      controls
      muted
      playsInline
      preload="auto"
      onLoadedMetadata={(e) => {
        e.currentTarget.currentTime = at
      }}
    />
  )
}

// Script approval: the exact prompts the video model will get (spec approve_script.md).
function ScriptApproval({ run, act }: { run: RunView; act: Act }) {
  const i = run.interrupt
  if (i?.kind !== 'approve_script' || !i.plan) return <p className="muted">No approval needed.</p>
  const script = i.script as Script | null
  const plan = i.plan
  const nameOf = Object.fromEntries((script?.characters ?? []).map((c) => [c.id, c.name]))
  const waiting = run.status === 'waiting_approval'
  const decide = (d: Decision) => act(() => api.approve(run.run_id, { script: d }))
  return (
    <div className="stack script-approval">
      <p>
        Nothing is paid for video yet. Below is exactly what goes to <b>{plan.model}</b> for each
        shot. Approve to generate, or rewrite the script with a different staging (the lines stay
        as they are).
      </p>
      {i.error && <div className="card bad">✗ {i.error}</div>}
      {script && (
        <div>
          <b>{script.title}</b> <span className="muted">· {script.style}</span>
          {script.characters.map((c) => (
            <div key={c.id} className="muted small character">
              {c.name}: {c.look} · <span className="pill">{c.voice_profile}</span> · voice{' '}
              <code>{voiceOf(run, c.id) ?? '—'}</code>
            </div>
          ))}
        </div>
      )}
      {plan.shots.map((s) => (
        <div key={s.shot_id} className="card plan-shot">
          <div className="row gap">
            <b>{s.shot_id}</b> <span className="speaker">{nameOf[s.character_id] ?? s.character_id}</span>
            <span className="pill">{s.delivery}</span>
            <span className="muted small">
              {s.start === 'text' ? 'from text' : `from the cut frame of ${s.source_shot}`} · shot{' '}
              {s.shot_s.toFixed(1)} s · clip {s.clip_s.toFixed(0)} s
            </span>
          </div>
          <div className="line">«{s.line}»</div>
          <pre className="prompt">{s.prompt}</pre>
        </div>
      ))}
      {plan.negative_prompt && (
        <div className="muted small">
          negative prompt: <code>{plan.negative_prompt}</code>
        </div>
      )}
      <div className="row gap">
        <button className="btn primary" disabled={!waiting} onClick={() => decide('approve')}>
          ✓ Approve script{i.next_cost_usd ? ` (~${usd(i.next_cost_usd)})` : ''}
        </button>
        <button className="btn" disabled={!waiting} onClick={() => decide('regenerate')}>
          ↻ Rewrite script
        </button>
      </div>
    </div>
  )
}

function Approve({ run, act }: { run: RunView; act: Act }) {
  const pending = run.interrupt?.kind === 'approve_cut_frames' ? run.interrupt.frames : []
  const [choice, setChoice] = useState<Record<string, Decision>>({})
  const byShot = Object.fromEntries(run.artifacts.keyframes.map((k) => [k.shot_id, k]))
  const clipOf = Object.fromEntries(
    run.artifacts.clips.filter((c) => c.stage === 'video').map((c) => [c.shot_id, c.url]),
  )
  if (!pending.length) return <p className="muted">No approval needed.</p>
  const ready = pending.every((f) => choice[f.shot_id])
  return (
    <div>
      {run.interrupt?.error && <div className="card bad">✗ {run.interrupt.error}</div>}
      <p>
        The next shot will continue exactly from the cut frame — where the edit cuts the previous
        shot. Compare the end of the video with the frame: if the frame is bad (blurry, no face),
        “↻ Another” regenerates the previous shot's video.
      </p>
      {pending.map((f) => {
        const src = f.source_shot ?? ''
        const at = f.at_s ?? 0
        return (
          <div key={f.shot_id} className={`cut-pair ${choice[f.shot_id] ?? ''}`}>
            <figure className="frame">
              {clipOf[src] && <ClipAt url={clipOf[src]} at={at} />}
              <figcaption className="muted small">
                end of video {src} · {at.toFixed(2)} s
              </figcaption>
            </figure>
            <figure className="frame">
              <img src={byShot[f.shot_id]?.url ?? ''} alt={`cut frame ${f.shot_id}`} />
              <figcaption className="muted small">cut frame → start of {f.shot_id}</figcaption>
            </figure>
            <div className="seg sm">
              {(['approve', 'regenerate'] as const).map((d) => (
                <button
                  key={d}
                  className={choice[f.shot_id] === d ? 'on' : ''}
                  onClick={() => setChoice({ ...choice, [f.shot_id]: d })}
                >
                  {d === 'approve' ? '✓ Approve' : `↻ Another ${src}`}
                </button>
              ))}
            </div>
          </div>
        )
      })}
      <button
        className="btn primary"
        disabled={!ready || run.status !== 'waiting_approval'}
        onClick={() => act(() => api.approve(run.run_id, choice))}
      >
        Continue
        {run.interrupt?.next_cost_usd ? ` (~$${run.interrupt.next_cost_usd.toFixed(2)})` : ''}
      </button>
    </div>
  )
}

function Regenerate({
  run,
  node,
  shot,
  act,
}: {
  run: RunView
  node: 'voice' | 'video' | 'lipsync'
  shot: string
  act: Act
}) {
  const busy = run.status === 'running' || run.status === 'queued'
  return (
    <button
      className="link"
      disabled={busy}
      title="New take of this artifact only (new seed); the rest comes from cache. Paid in Real mode"
      onClick={() => act(() => api.regenerate(run.run_id, node, shot))}
    >
      ↻ regenerate
    </button>
  )
}

function Events({ run, node }: { run: RunView; node: string }) {
  const events = run.events.filter((e) => e.node === node)
  if (!events.length) return null
  return (
    <details className="events">
      <summary>Trace events ({events.length})</summary>
      <ul>
        {events.map((e, i) => (
          <li key={i}>
            <code>{String(e.event)}</code> {e.what ? String(e.what) : ''}{' '}
            {e.error ? <span className="err">{String(e.error)}</span> : null}
          </li>
        ))}
      </ul>
    </details>
  )
}

export function Inspector({ run, node, act }: { run: RunView; node: string; act: Act }) {
  const a = run.artifacts
  const script = a.script as Script | null
  const view = run.nodes.find((n) => n.name === node)
  // Who says the shot's line: the character's name (several characters, several voices).
  const speakerOf = Object.fromEntries(
    (script?.shots ?? []).map((s) => [
      s.id,
      script?.characters.find((c) => c.id === s.character_id)?.name ?? s.character_id,
    ]),
  )

  return (
    <aside className="inspector">
      <h3>{node}</h3>
      {view?.error && <div className="card bad">✗ {view.error}</div>}

      {node === 'script' &&
        (script ? (
          <div className="stack">
            <div>
              <b>{script.title}</b> <span className="muted">· {script.style}</span>
            </div>
            {script.characters.map((c) => (
              <div key={c.id} className="card">
                <b>{c.name}</b> <span className="pill">{c.voice_profile}</span>{' '}
                <span className="muted small">voice {voiceOf(run, c.id) ?? '—'}</span>
                <div className="muted small">{c.look}</div>
              </div>
            ))}
            {script.shots.map((s) => (
              <div key={s.id} className="card">
                <div className="row gap">
                  <b>{s.id}</b>{' '}
                  <span className="speaker">
                    {script.characters.find((c) => c.id === s.character_id)?.name ?? s.character_id}
                  </span>
                  <span className="pill">{s.delivery}</span>
                  <span className="muted small">{s.camera}</span>
                </div>
                <div className="line">«{s.line}»</div>
                <div className="muted small">{s.visual_prompt}</div>
              </div>
            ))}
            <p className="muted small">
              Lines are inserted by code from the input — the model's schema has no <code>line</code> field.
            </p>
          </div>
        ) : (
          <p className="muted">Not generated yet.</p>
        ))}

      {node === 'voice' && (
        <div className="stack">
          {a.voices.map((v) => (
            <div key={v.shot_id} className="card">
              <div className="row between">
                <span>
                  <b>{v.shot_id}</b>{' '}
                  <span className="speaker">{speakerOf[v.shot_id] ?? ''}</span>
                </span>
                <Regenerate run={run} node="voice" shot={v.shot_id} act={act} />
              </div>
              {v.url && <audio src={v.url} controls />}
              <div className="small">
                TTS request: <code>{v.tts_text}</code>
              </div>
              <div className="muted small">
                voice {v.voice_id} · seed {v.seed}
              </div>
            </div>
          ))}
          {!a.voices.length && <p className="muted">No voice yet.</p>}
        </div>
      )}

      {node === 'keyframe' && (
        <div className="frames">
          {a.keyframes.map((k) => (
            <figure key={k.shot_id} className="frame">
              {k.url && <img src={k.url} alt={k.shot_id} />}
              <figcaption>
                <b>
                  start of {k.shot_id} {k.approved && <span className="ok">✓</span>}
                </b>
                <div className="muted small">
                  frame of {k.source_shot} at {k.at_s?.toFixed(2)} s
                </div>
              </figcaption>
            </figure>
          ))}
          {!a.keyframes.length && (
            <p className="muted">A cut frame exists only between consecutive shots of the same speaker.</p>
          )}
        </div>
      )}

      {node === 'approve_script' && <ScriptApproval run={run} act={act} />}
      {node === 'approve' && <Approve run={run} act={act} />}

      {node === 'video' && (
        <div className="frames">
          {a.clips.filter((c) => c.stage === 'video').map((c) => (
            <figure key={c.shot_id} className="frame">
              {c.url && <video src={c.url} controls muted playsInline />}
              <figcaption className="row between">
                <b>{c.shot_id}</b>
                <Regenerate run={run} node="video" shot={c.shot_id} act={act} />
              </figcaption>
            </figure>
          ))}
          {!a.clips.length && <p className="muted">Not generated yet.</p>}
        </div>
      )}

      {node === 'assemble' &&
        (a.final ? (
          <video src={a.final} controls playsInline className="final sm" />
        ) : (
          <p className="muted">Not edited yet.</p>
        ))}

      {node === 'voice_check' && (
        <div className="stack">
          <p className="muted small">
            The voice is checked before video is generated: a bad take → a new take of this shot only.
          </p>
          {run.voice_checks.map((c) => (
            <div key={c.shot_id} className="card">
              <div className="row between">
                <b>{c.shot_id}</b>
                <span className={c.passed ? 'ok' : 'err'}>
                  {c.passed ? '✓ verbatim' : `✗ WER ${c.wer}`}
                </span>
              </div>
              <div className="small">
                heard: <code>{c.heard}</code>
              </div>
              {c.attempt > 0 && <div className="muted small">take #{c.attempt + 1}</div>}
            </div>
          ))}
          {!run.voice_checks.length && <p className="muted">Not checked yet.</p>}
        </div>
      )}

      {node === 'lipsync' && (
        <>
          <p className="muted">
            The model fits the lips to our shot audio. Only the video is taken from its output — the
            audio in the final is always ours, so it cannot change the words.
          </p>
          <div className="frames">
            {a.clips.filter((c) => c.stage === 'lipsync').map((c) => (
              <figure key={c.shot_id} className="frame">
                {c.url && <video src={c.url} controls muted playsInline />}
                <figcaption className="row between">
                  <b>{c.shot_id}</b>
                  <Regenerate run={run} node="lipsync" shot={c.shot_id} act={act} />
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}

      {node === 'sync_check' && <SyncPanel run={run} />}

      {node === 'verify' &&
        (run.asr ? <AsrDiff asr={run.asr} /> : <p className="muted">Not checked yet.</p>)}

      <Events run={run} node={node} />
    </aside>
  )
}
