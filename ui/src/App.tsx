import { useEffect, useState } from 'react'
import { api, statusLabel, usd, type RunSummary } from './api'
import { NewRun } from './NewRun'
import { RunPage } from './RunPage'

// Hash routing: #/new or #/run/<id>. Two screens need no router.
function useRoute(): [string, (r: string) => void] {
  const [route, setRoute] = useState(() => location.hash.slice(1) || '/new')
  useEffect(() => {
    const on = () => setRoute(location.hash.slice(1) || '/new')
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return [route, (r) => (location.hash = r)]
}

export default function App() {
  const [route, go] = useRoute()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [offline, setOffline] = useState(false)
  const runId = route.startsWith('/run/') ? route.slice(5) : null
  // "Run again": the form opens filled from a failed run (#/new?from=<id>).
  const from = route.startsWith('/new?') ? new URLSearchParams(route.slice(5)).get('from') : null

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .runs()
        .then((r) => {
          if (!alive) return
          setRuns(r)
          setOffline(false)
        })
        .catch(() => alive && setOffline(true))
    load()
    const t = setInterval(load, 4000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-dot" /> Pipeline Studio
        </div>
        <button className="btn primary block" onClick={() => go('/new')}>
          + New run
        </button>
        {offline && <div className="card bad small">backend unreachable — run make ui</div>}
        <div className="side-title">Runs</div>
        <ul className="runs">
          {runs.map((r) => (
            <li key={r.run_id}>
              <a className={r.run_id === runId ? 'active' : ''} href={`#/run/${r.run_id}`}>
                <span className="run-idea">{r.idea}</span>
                <span className="run-meta">
                  <span className={`badge s-${r.status}`}>{statusLabel(r.status)}</span>
                  <span>{r.run_id}</span>
                  <span>{usd(r.cost_usd)}</span>
                </span>
              </a>
            </li>
          ))}
          {runs.length === 0 && <li className="muted pad">No runs yet</li>}
        </ul>
      </aside>
      <main className="main">
        {runId ? (
          <RunPage key={runId} runId={runId} />
        ) : (
          <NewRun key={from ?? ''} from={from} onStarted={(id) => go(`/run/${id}`)} />
        )}
      </main>
    </div>
  )
}
