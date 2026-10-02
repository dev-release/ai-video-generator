import { useEffect, useMemo, useState } from 'react'
import { api, ApiError, usd, type Limits, type Mode, type PreflightReport } from './api'
import { lineIssues, words } from './text'

export function NewRun({
  from,
  onStarted,
}: {
  from?: string | null
  onStarted: (id: string) => void
}) {
  const [lim, setLim] = useState<Limits | null>(null)
  const [idea, setIdea] = useState('A girl finds a letter from her future self')
  const [lines, setLines] = useState(['This handwriting is mine, but I never wrote this letter.'])
  const [mode, setMode] = useState<Mode>('fake')
  // Like the CLI: one start -> a video without stops. Script and frame approval are optional.
  const [review, setReview] = useState(false)
  const [fresh, setFresh] = useState(false)
  const [report, setReport] = useState<{ key: string; r: PreflightReport } | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .limits()
      .then(setLim)
      .catch((e: Error) => setErrors([e.message]))
  }, [])

  useEffect(() => {
    if (!from) return
    api
      .run(from)
      .then((r) => {
        const b = r.brief as { idea?: string; lines?: string[] } | null
        if (b?.idea) setIdea(b.idea)
        if (b?.lines?.length) setLines(b.lines)
        if (r.mode === 'fake' || r.mode === 'real') setMode(r.mode)
        setReview(r.auto_approve === false)
      })
      .catch((e: Error) => setErrors([e.message]))
  }, [from])

  // Empty fields are not sent: the backend then takes the lines from the quotes in the idea.
  const body = {
    idea,
    lines: lines.filter((l) => l.trim()),
    mode,
    auto_approve: !review,
    fresh,
  }
  const total = useMemo(() => lines.reduce((a, l) => a + words(l), 0), [lines])
  const issues = lim ? lines.map((l) => lineIssues(l, lim)) : []
  const invalid =
    !lim || issues.some((i) => i.length) || total > lim.max_words_total || !idea.trim()

  // The estimate is valid only for the input it was computed for.
  const key = JSON.stringify([idea, lines, mode, fresh])
  const shown = report?.key === key ? report.r : null

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setErrors([])
    try {
      await fn()
    } catch (e) {
      setErrors(e instanceof ApiError ? e.problems : [(e as Error).message])
    } finally {
      setBusy(false)
    }
  }

  const estimate = () => run(async () => setReport({ key, r: await api.preflight(body) }))
  // Real mode: price first, then an explicit confirmation — never a blind paid start.
  const needsConfirm = mode === 'real' && !shown
  const start = () =>
    run(async () => {
      if (needsConfirm) {
        setReport({ key, r: await api.preflight(body) })
        return
      }
      onStarted((await api.start(body)).run_id)
    })

  if (!lim) return <div className="page muted">Loading…</div>

  return (
    <div className="page narrow">
      <h1>New run</h1>
      <p className="lead">
        Lines are spoken <b>word for word</b>. The model invents the character, emotion and shots,
        but never sees the lines as an editable field — the code inserts them.
      </p>

      <label className="field">
        <span>
          Idea (1–3 sentences)
          <em className="counter">you can put the line in quotes right here</em>
        </span>
        <textarea rows={2} value={idea} maxLength={500} onChange={(e) => setIdea(e.target.value)} />
      </label>

      {lines.map((line, i) => (
        <label className="field" key={i}>
          <span>
            Line {i + 1}
            <em className="counter">{words(line)} words</em>
            {lines.length > 1 && (
              <button
                className="link"
                onClick={() => setLines(lines.filter((_, j) => j !== i))}
                type="button"
              >
                remove
              </button>
            )}
          </span>
          <textarea
            rows={2}
            className={issues[i]?.length ? 'invalid' : ''}
            value={line}
            onChange={(e) => setLines(lines.map((l, j) => (j === i ? e.target.value : l)))}
          />
          {issues[i]?.map((m) => (
            <small className="err" key={m}>
              {m}
            </small>
          ))}
        </label>
      ))}
      <div className="row between">
        {lines.length < lim.max_lines ? (
          <button className="btn ghost" onClick={() => setLines([...lines, ''])}>
            + second line (second shot)
          </button>
        ) : (
          <span />
        )}
        <span className={total > lim.max_words_total ? 'err' : 'muted'}>
          total {total} / {lim.max_words_total} words
        </span>
      </div>

      <div className="options">
        <div className="seg">
          {(['fake', 'real'] as const).map((m) => (
            <button key={m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)}>
              {m === 'fake' ? 'Fake · $0, local' : 'Real · paid APIs'}
            </button>
          ))}
        </div>
        <label className="check" title="Do not reuse paid artifacts from the shared cache">
          <input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
          fresh generation
        </label>
        <label
          className="check"
          title="Before paying for video: approve the script with the exact video prompts, then each cut frame between shots"
        >
          <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} />
          review before video
        </label>
      </div>
      <p className="muted small models">
        Models:{' '}
        {Object.entries(lim.models_by_mode[mode] ?? {})
          .map(([cap, name]) => `${cap} ${name}`)
          .join(' · ')}
      </p>
      {(lim.notes_by_mode[mode] ?? []).map((n) => (
        <p className="small model-note" key={n}>
          ⓘ {n}
        </p>
      ))}
      <p className="muted small">
        Cost cap per run {usd(lim.max_run_cost_usd)}, per day {usd(lim.max_daily_cost_usd)} — set in
        .env; the agent never raises them.
      </p>

      {shown && (
        <div className={`card ${shown.ok ? '' : 'bad'}`}>
          <b>
            Estimate: ~{usd(shown.estimate_usd)} (up to {usd(shown.worst_usd)} with retries) · left
            today {usd(shown.daily_left_usd)}
          </b>
          {shown.warnings.map((w) => (
            <div className="warn" key={w}>
              ! {w}
            </div>
          ))}
          {shown.problems.map((p) => (
            <div className="err" key={p}>
              ✗ {p}
            </div>
          ))}
        </div>
      )}
      {errors.length > 0 && (
        <div className="card bad">
          {errors.map((e) => (
            <div className="err" key={e}>
              ✗ {e}
            </div>
          ))}
        </div>
      )}

      <div className="row gap">
        <button className="btn" disabled={invalid || busy} onClick={estimate}>
          Check & estimate
        </button>
        <button
          className="btn primary"
          disabled={invalid || busy || (mode === 'real' && shown !== null && !shown.ok)}
          onClick={start}
        >
          {mode === 'real'
            ? shown
              ? `Confirm run for ~${usd(shown.estimate_usd)}`
              : 'Show price & run'
            : 'Run'}
        </button>
      </div>
    </div>
  )
}
