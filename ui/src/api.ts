// Thin FastAPI client. Types are generated from OpenAPI (make ui-types), not written by hand.
import type { components } from './api-schema'

type S = components['schemas']
export type RunView = S['RunView']
export type RunSummary = S['RunSummary']
export type NodeView = S['NodeView']
export type Limits = S['Limits']
export type AsrCheck = S['AsrCheck']
export type VoiceCheck = S['VoiceCheck']
export type SyncCheck = S['SyncCheck']
export type ShotSync = S['ShotSync']
export type VideoPlan = S['VideoPlan']
export type Mode = 'fake' | 'real'
export type Decision = 'approve' | 'regenerate'

export interface StartBody {
  idea: string
  lines: string[]
  mode: Mode
  auto_approve: boolean
  fresh?: boolean
}

export interface PreflightReport {
  ok: boolean
  problems: string[]
  warnings: string[]
  estimate_usd: number
  worst_usd: number
  max_run_usd: number
  daily_left_usd: number
}

export class ApiError extends Error {
  readonly status: number
  readonly problems: string[]

  constructor(status: number, problems: string[]) {
    super(problems.join('\n'))
    this.status = status
    this.problems = problems
  }
}

// 422 from pydantic ([{loc, msg}]) and 409 from preflight ({problems}) -> readable lines.
function problemsOf(body: unknown): string[] {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (Array.isArray(detail)) {
    return detail.map((d: { loc?: unknown[]; msg?: string }) =>
      `${(d.loc ?? []).filter((x) => x !== 'body').join('.')}: ${d.msg ?? ''}`.replace(
        /^Value error, /,
        '',
      ),
    )
  }
  if (detail && typeof detail === 'object' && 'problems' in detail) {
    return (detail as { problems: string[] }).problems
  }
  return [typeof detail === 'string' ? detail : 'unknown error']
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) throw new ApiError(res.status, problemsOf(await res.json().catch(() => null)))
  return (await res.json()) as T
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  limits: () => call<Limits>('/api/limits'),
  runs: () => call<RunSummary[]>('/api/runs'),
  run: (id: string) => call<RunView>(`/api/runs/${id}`),
  preflight: (b: StartBody) => post<PreflightReport>('/api/preflight', b),
  start: (b: StartBody) => post<{ run_id: string }>('/api/runs', b),
  approve: (id: string, decisions: Record<string, Decision>) =>
    post(`/api/runs/${id}/approve`, { decisions }),
  regenerate: (id: string, node: 'voice' | 'keyframe' | 'video' | 'lipsync', shot_id: string) =>
    post(`/api/runs/${id}/regenerate`, { node, shot_id }),
  resume: (id: string) => post(`/api/runs/${id}/resume`, {}),
  rewrite: (id: string) => post(`/api/runs/${id}/rewrite`, {}),
  eventsUrl: (id: string) => `/api/runs/${id}/events`,
}

const STATUS_LABEL: Record<string, string> = {
  done: 'done',
  failed_verify: 'not verbatim',
  failed_voice: 'voice not verbatim',
  failed_sync: 'lips out of sync',
  waiting_approval: 'awaiting approval',
  running: 'running',
  queued: 'queued',
  error: 'error',
  incomplete: 'incomplete',
}

export const statusLabel = (s: string) => STATUS_LABEL[s] ?? s

export const usd = (v: number) => `$${v.toFixed(v < 1 ? 3 : 2)}`
