import type { Priority, SecurityStatus, Status } from '../api'

// Every status carries an icon name and a text label, so meaning never depends on colour alone.
export const STATUS_META: Record<Status, { label: string; color: string; icon: string; tone: string }> = {
  FULLY_RECOVERED: { label: 'Fully recovered', color: '#22a06b', icon: 'check', tone: 'text-emerald-800 bg-emerald-50 ring-emerald-300' },
  MOSTLY_RECOVERED: { label: 'Mostly recovered', color: '#1fa6a6', icon: 'check-half', tone: 'text-teal-800 bg-teal-50 ring-teal-300' },
  PARTIALLY_RECOVERED: { label: 'Partially recovered', color: '#f4a340', icon: 'half', tone: 'text-amber-900 bg-amber-50 ring-amber-300' },
  FRAGMENT_ONLY: { label: 'Fragment only', color: '#e07b39', icon: 'puzzle', tone: 'text-orange-900 bg-orange-50 ring-orange-300' },
  UNCERTAIN: { label: 'Uncertain', color: '#7a5bc7', icon: 'question', tone: 'text-violet-800 bg-violet-50 ring-violet-300' },
  UNRECOVERABLE: { label: 'Unrecoverable', color: '#d9534f', icon: 'x', tone: 'text-red-800 bg-red-50 ring-red-300' },
}

export const PRIORITY_META: Record<Priority, { color: string; tone: string; rank: number }> = {
  CRITICAL: { color: '#d9534f', tone: 'text-white bg-danger ring-danger', rank: 0 },
  HIGH: { color: '#e07b39', tone: 'text-orange-900 bg-orange-100 ring-orange-300', rank: 1 },
  MEDIUM: { color: '#f4a340', tone: 'text-amber-900 bg-amber-100 ring-amber-300', rank: 2 },
  LOW: { color: '#3282b8', tone: 'text-sky-900 bg-sky-50 ring-sky-300', rank: 3 },
  INFORMATIONAL: { color: '#6b7c83', tone: 'text-ink2 bg-track ring-line2', rank: 4 },
}

export const SECURITY_META: Record<SecurityStatus, { label: string; short: string; color: string; icon: string; tone: string }> = {
  CLEAN: { label: 'Clean', short: 'SAFE', color: '#22a06b', icon: 'shield-check', tone: 'text-emerald-800 bg-emerald-50 ring-emerald-300' },
  SUSPICIOUS: { label: 'Suspicious', short: 'SUSPICIOUS', color: '#f4a340', icon: 'shield-alert', tone: 'text-amber-900 bg-amber-50 ring-amber-400' },
  MALWARE_DETECTED: { label: 'Malware detected', short: 'MALWARE DETECTED', color: '#d9534f', icon: 'shield-x', tone: 'text-white bg-danger ring-danger' },
  SCAN_FAILED: { label: 'Scan failed', short: 'SCAN FAILED', color: '#7a5bc7', icon: 'shield-question', tone: 'text-violet-800 bg-violet-50 ring-violet-300' },
  NOT_SCANNED: { label: 'Not scanned', short: 'NOT SCANNED', color: '#6b7c83', icon: 'shield-off', tone: 'text-ink2 bg-track ring-line2' },
}

// categorical slots (light-mode steps) in fixed order: identity follows the file type, never its rank
export const TYPE_COLOR: Record<string, string> = {
  pdf: '#2a78d6', docx: '#eb6834', jpeg: '#1baf7a', sqlite: '#eda100', log: '#e87ba4', txt: '#4a3aa7',
  zip: '#e34948', xlsx: '#008300', bin: '#6b7c83',
}

export const FAMILY_COLOR: Record<string, string> = {
  jpeg: '#1baf7a', pdf: '#2a78d6', sqlite: '#eda100', compressed: '#8a9aa0', text: '#a9b5ba', log: '#e87ba4',
  xml: '#4a3aa7', fat_dir: '#0f4c5c', binary: '#c3ccd0', zero: '#f3ecdd',
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

export const pct = (n: number | null | undefined, d = 0) => (n == null ? 'N/A' : `${n.toFixed(d)}%`)
export const num = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString())

export function relLabel(conf: number): string {
  if (conf >= 90) return 'Strong'
  if (conf >= 75) return 'Probable'
  if (conf >= 55) return 'Possible'
  if (conf >= 35) return 'Weak'
  return 'Uncertain'
}

export function scoreColor(v: number): string {
  if (v >= 85) return '#22a06b'
  if (v >= 65) return '#1fa6a6'
  if (v >= 45) return '#f4a340'
  if (v >= 25) return '#e07b39'
  return '#d9534f'
}

export const niceType = (t: string) => ({ jpeg: 'JPEG', pdf: 'PDF', docx: 'DOCX', sqlite: 'SQLite', log: 'LOG', txt: 'TXT', zip: 'ZIP', xlsx: 'XLSX', bin: 'BINARY' } as Record<string, string>)[t] ?? t.toUpperCase()

export function plural(n: number, word: string, pluralWord?: string): string {
  return `${n.toLocaleString()} ${n === 1 ? word : pluralWord ?? `${word}s`}`
}

/** SHA-256 of a byte buffer in the browser (WebCrypto); used to prove export byte identity. */
export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', buf)
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('')
}
