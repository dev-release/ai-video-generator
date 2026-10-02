import type { Limits } from './api'

export const words = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

// Form hints repeat the Python rules only for quick feedback; the backend decides (422).
export function lineIssues(line: string, lim: Limits): string[] {
  const out: string[] = []
  const bad = [...new Set([...line].filter((c) => lim.forbidden_chars.includes(c)))]
  if (bad.length) out.push(`forbidden characters ${bad.join(' ')} — emotion is chosen separately`)
  const n = words(line)
  if (line.trim() && (n < lim.min_words_per_line || n > lim.max_words_per_line))
    out.push(`${n} words, allowed ${lim.min_words_per_line}–${lim.max_words_per_line}`)
  if (line !== line.trim()) out.push('leading or trailing spaces')
  return out
}
