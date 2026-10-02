import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, statusLabel, usd, type RunView } from './api'
import { Graph } from './Graph'
import { AsrDiff, Inspector } from './Inspector'

const LIVE = new Set(['queued', 'running'])

// Run state: SSE from trace.jsonl as a "something changed" signal + re-reading the summary state.
function useRun(runId: string) {
  const [run, setRun] = useState<RunView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const reload = useCallback(() => {
    api
      .run(runId)
      .then((r) => {
        setRun(r)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [runId])

  useEffect(() => {
    reload()
    const es = new EventSource(api.eventsUrl(runId))
    es.onmessage = () => {
      clearTimeout(timer.current)
      timer.current = window.setTimeout(reload, 150)
    }
    return () => {
      es.close()
      clearTimeout(timer.current)
    }
  }, [runId, reload])

  // Queued/running status lives in API memory, not in the trace, so poll while it is active.
  useEffect(() => {
    if (!run || !LIVE.has(run.status)) return
    const t = setInterval(reload, 1500)
    return () => clearInterval(t)
  }, [run, reload])

  return { run, error, reload }
}

export function RunPage({ runId }: { runId: string }) {
  const { run, error, reload } = useRun(runId)
  const [picked, setPicked] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const result = useRef<HTMLElement>(null)
  const finalUrl = run?.artifacts.final

  // A new video (run finished, new take): show it even if the user scrolled down to the
  // approval. "nearest": already visible -> the page does not move.
  useEffect(() => {
    if (finalUrl) result.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [finalUrl])

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(null)
    try {
      await fn()
      reload()
    } catch (e) {
      setActionError(e instanceof ApiError ? e.problems.join('; ') : (e as Error).message)
    }
  }

  if (error) return <div className="page err">✗ {error}</div>
  if (!run) return <div className="page muted">Loading…</div>

  // Until the user picks something, show the stage that matters now.
  const auto =
    run.status === 'waiting_approval'
      ? run.interrupt?.kind === 'approve_script'
        ? 'approve_script'
        : 'approve'
      : run.status === 'done' || run.status === 'failed_verify'
        ? 'verify'
        : (run.nodes.find((n) => n.status === 'running')?.name ?? 'script')
  const selected = picked ?? auto
  const pct = Math.min(100, (run.cost_usd / run.max_run_cost_usd) * 100)
  const total = run.nodes.reduce((a, n) => a + n.duration_s, 0)

  return (
    <div className="run">
      <header className="run-head">
        <div>
          <div className="row gap">
            <span className={`badge big s-${run.status}`}>{statusLabel(run.status)}</span>
            <span className="muted">
              {run.run_id} · {run.mode}
            </span>
          </div>
          <h2>{run.brief?.idea as string}</h2>
          <ol className="lines">
            {((run.brief?.lines as string[]) ?? []).map((l) => (
              <li key={l}>«{l}»</li>
            ))}
          </ol>
        </div>
        <div className="stats">
          <div>
            <span className="muted">cost</span>
            <b>
              {usd(run.cost_usd)} <span className="muted">/ {usd(run.max_run_cost_usd)}</span>
            </b>
            <div className="meter">
              <i style={{ width: `${pct}%` }} />
            </div>
          </div>
          <div>
            <span className="muted">stage time</span>
            <b>{total.toFixed(1)} s</b>
          </div>
          <div>
            <span className="muted">WER</span>
            <b className={run.asr ? (run.asr.passed ? 'ok' : 'err') : ''}>
              {run.asr ? run.asr.wer : '—'}
            </b>
          </div>
        </div>
      </header>

      {run.rejected && (
        <div className="card bad rejected">
          <b>
            ✗ The video model's content checker refused
            {run.rejected.shot_id ? ` shot ${run.rejected.shot_id}` : ' a request'}.
          </b>{' '}
          This happens to harmless prompts too. Resuming would send the same prompt again, so choose
          a way out:
          <div className="row">
            <button className="btn primary" onClick={() => act(() => api.rewrite(run.run_id))}>
              ✎ Rewrite script
            </button>
            {run.rejected.shot_id &&
              (run.rejected.node === 'video' || run.rejected.node === 'lipsync') && (
                <button
                  className="btn"
                  onClick={() =>
                    act(() =>
                      api.regenerate(
                        run.run_id,
                        run.rejected!.node as 'video' | 'lipsync',
                        run.rejected!.shot_id!,
                      ),
                    )
                  }
                >
                  ↻ New take of {run.rejected.shot_id}
                </button>
              )}
            <RunAgain run={run} />
          </div>
          <span className="muted small">
            Rewrite: same lines, new staging and video prompts. New take: the same prompt once more.
            Run again: a new run from the start with the same text.
          </span>
          <details className="small">
            <summary>details</summary>
            {run.error}
          </details>
        </div>
      )}
      {run.error && !run.rejected && (
        <div className="card bad">
          ✗ {run.error}
          {run.status === 'error' && (
            <div>
              <button className="btn" onClick={() => act(() => api.resume(run.run_id))}>
                Resume from where it stopped
              </button>{' '}
              <RunAgain run={run} />{' '}
              <span className="muted small">
                paid work is not repeated: artifacts come from cache, fal jobs are picked up
              </span>
            </div>
          )}
        </div>
      )}
      {actionError && <div className="card bad">✗ {actionError}</div>}

      {/* The finished video with voice is what the user looks for after a start: above the graph. */}
      {run.artifacts.final && (
        <section className="result" ref={result}>
          <video src={run.artifacts.final} controls playsInline className="final" />
          <div className="grow">
            <h3>Result</h3>
            {run.asr && <AsrDiff asr={run.asr} />}
            <a className="btn" href={run.artifacts.final} download>
              Download final.mp4
            </a>
          </div>
        </section>
      )}

      <div className="run-body">
        <Graph nodes={run.nodes} models={run.models} selected={selected} onSelect={setPicked} />
        <Inspector run={run} node={selected} act={act} />
      </div>
    </div>
  )
}

// The whole process again: the form opens with this run's text, mode and review; the price is
// shown and confirmed there, as for any start. Finished paid artifacts come from the shared cache.
function RunAgain({ run }: { run: RunView }) {
  return (
    <button className="btn" onClick={() => (location.hash = `/new?from=${run.run_id}`)}>
      ↻ Run again from the start
    </button>
  )
}
