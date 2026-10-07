import {
  AlertTriangle, CheckCircle2, CircleDashed, CircleHelp, CircleSlash, Info, Loader2, PieChart, Puzzle,
  ShieldAlert, ShieldCheck, ShieldOff, ShieldQuestion, ShieldX, XCircle, type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { Priority, SecurityStatus, Status } from '../api'
import { PRIORITY_META, SECURITY_META, STATUS_META, scoreColor } from '../lib/format'

const ICONS: Record<string, LucideIcon> = {
  check: CheckCircle2, 'check-half': PieChart, half: CircleDashed, puzzle: Puzzle, question: CircleHelp, x: XCircle,
  'shield-check': ShieldCheck, 'shield-alert': ShieldAlert, 'shield-x': ShieldX, 'shield-question': ShieldQuestion, 'shield-off': ShieldOff,
}

export function Card({ children, className = '', title, action, subtitle, icon: Icon, accent = '#1fa6a6', bodyClass = 'p-5' }: {
  children: ReactNode; className?: string; title?: ReactNode; action?: ReactNode; subtitle?: ReactNode
  icon?: LucideIcon; accent?: string; bodyClass?: string
}) {
  return (
    <section className={`rounded-2xl border border-line bg-card shadow-[var(--shadow-card)] ${className}`}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="flex min-w-0 items-start gap-2.5">
            {Icon && (
              <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg" style={{ background: `${accent}1f`, color: accent }}>
                <Icon className="h-4 w-4" />
              </span>
            )}
            <div className="min-w-0">
              <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
              {subtitle && <p className="mt-0.5 text-xs leading-relaxed text-muted">{subtitle}</p>}
            </div>
          </div>
          {action}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

function Pill({ tone, icon, children, size = 'sm', title }: { tone: string; icon?: string; children: ReactNode; size?: 'sm' | 'md'; title?: string }) {
  const I = icon ? ICONS[icon] : null
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full ring-1 ring-inset font-semibold ${size === 'md' ? 'px-3 py-1 text-xs' : 'px-2.5 py-0.5 text-[11px]'} ${tone}`}>
      {I && <I className={size === 'md' ? 'h-3.5 w-3.5' : 'h-3 w-3'} aria-hidden />}{children}
    </span>
  )
}

export function StatusBadge({ status, size = 'sm' }: { status: Status; size?: 'sm' | 'md' }) {
  const m = STATUS_META[status]
  return <Pill tone={m.tone} icon={m.icon} size={size}>{m.label.toUpperCase()}</Pill>
}

export function PriorityBadge({ level }: { level: Priority }) {
  const m = PRIORITY_META[level]
  return <Pill tone={m.tone}>{level}</Pill>
}

export function SecurityBadge({ status, test, size = 'sm', title }: { status?: SecurityStatus | null; test?: boolean; size?: 'sm' | 'md'; title?: string }) {
  const m = SECURITY_META[status ?? 'NOT_SCANNED']
  return <Pill tone={m.tone} icon={m.icon} size={size} title={title}>{m.short}{test ? ' (TEST)' : ''}</Pill>
}

export function ScoreBar({ value, label, suffix = '', muted = false }: { value: number | null; label?: string; suffix?: string; muted?: boolean }) {
  const v = value ?? 0
  return (
    <div className="min-w-[88px]">
      <div className="flex items-baseline justify-between gap-2">
        {label && <span className="text-[11px] text-muted">{label}</span>}
        <span className="font-mono text-xs font-semibold tabular-nums text-ink">{value == null ? 'N/A' : `${v.toFixed(0)}${suffix}`}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-track">
        {value != null && <div className="h-full rounded-full" style={{ width: `${Math.max(2, Math.min(100, v))}%`, background: muted ? '#8a9aa0' : scoreColor(v) }} />}
      </div>
    </div>
  )
}

/** Circular indicator for one separate measure. N/A is shown as N/A, never as a number. */
export function Gauge({ value, label, sub, suffix = '%', size = 92 }: { value: number | null | undefined; label: string; sub?: string; suffix?: string; size?: number }) {
  const r = (size - 12) / 2
  const c = 2 * Math.PI * r
  const v = value == null ? 0 : Math.max(0, Math.min(100, value))
  const col = value == null ? '#c3ccd0' : scoreColor(v)
  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${label}: ${value == null ? 'not available' : `${v.toFixed(0)}${suffix}`}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#eee6d6" strokeWidth="9" />
        {value != null && <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={col} strokeWidth="9" strokeLinecap="round"
          strokeDasharray={`${(v / 100) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />}
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="font-mono" fontSize={value == null ? 15 : 19} fontWeight="700" fill="#17323a">
          {value == null ? 'N/A' : `${v.toFixed(0)}`}
        </text>
      </svg>
      <div className="min-w-0">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</div>
        <div className="font-mono text-lg font-bold text-ink">{value == null ? 'N/A' : `${v.toFixed(value % 1 ? 1 : 0)}${suffix === '%' ? '%' : ` / 100`}`}</div>
        {sub && <div className="text-[11px] leading-snug text-muted">{sub}</div>}
      </div>
    </div>
  )
}

export function Stat({ label, value, hint, accent = '#1fa6a6', icon: Icon }: { label: string; value: ReactNode; hint?: ReactNode; accent?: string; icon?: LucideIcon }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-line bg-card p-4 shadow-[var(--shadow-card)]">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: accent }} />
      <div className="flex items-start justify-between gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</div>
        {Icon && <span className="grid h-8 w-8 place-items-center rounded-xl" style={{ background: `${accent}1f`, color: accent }}><Icon className="h-4 w-4" /></span>}
      </div>
      <div className="mt-1 font-mono text-[26px] font-bold leading-tight tabular-nums text-ink">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin text-teal" />{label ?? 'Loading…'}</div>
}

/** Errors always carry a title and a reason (e.g. EXPORT FAILED / reason). */
export function ErrorBox({ error, title = 'Something went wrong' }: { error: unknown; title?: string }) {
  return (
    <div className="flex gap-3 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger" />
      <div><div className="font-bold uppercase tracking-wide">{title}</div><div className="mt-0.5">Reason: {String((error as Error)?.message ?? error)}</div></div>
    </div>
  )
}

export function Hint({ children }: { children: ReactNode }) {
  return (
    <div className="flex gap-2 rounded-xl border border-teal/25 bg-tealsoft px-3 py-2 text-xs leading-relaxed text-ink2">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />
      <div>{children}</div>
    </div>
  )
}

export function DemoBanner() {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-accent/50 bg-gradient-to-r from-[#fff1d9] to-[#fff8ea] px-4 py-2.5 text-xs text-amber-950">
      <span className="rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-bold tracking-wider text-white">DEMO MODE</span>
      <span><b>Simulated evidence.</b> This storage image was generated synthetically with simulated damage. Every number is genuine engine output on synthetic data and is <b>not</b> a forensic finding about real evidence.</span>
    </div>
  )
}

export function CheckIcon({ status }: { status: string }) {
  const map: Record<string, [LucideIcon, string]> = {
    pass: [CheckCircle2, 'text-success'], fail: [XCircle, 'text-danger'], partial: [CircleDashed, 'text-accent'], na: [CircleSlash, 'text-muted'],
  }
  const [I, cls] = map[status] ?? [CircleHelp, 'text-muted']
  return <I className={`inline-block h-4 w-4 shrink-0 ${cls}`} aria-label={status} />
}

export type DataKind = 'observed' | 'derived' | 'inferred' | 'reconstructed' | 'unverified' | 'simulated' | 'missing' | 'corrupted'
const DATA_TONE: Record<DataKind, string> = {
  observed: 'text-sky-900 ring-sky-300 bg-sky-50',
  derived: 'text-ink2 ring-line2 bg-track',
  inferred: 'text-violet-800 ring-violet-300 bg-violet-50',
  reconstructed: 'text-emerald-800 ring-emerald-300 bg-emerald-50',
  unverified: 'text-amber-900 ring-amber-300 bg-amber-50',
  simulated: 'text-fuchsia-800 ring-fuchsia-300 bg-fuchsia-50',
  missing: 'text-red-800 ring-red-300 bg-red-50',
  corrupted: 'text-orange-900 ring-orange-300 bg-orange-50',
}

export function DataClass({ kind }: { kind: DataKind }) {
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ring-1 ring-inset ${DATA_TONE[kind]}`}>{kind}</span>
}

/** Map a backend data_class string ("OBSERVED (…)", "INFERRED RELATIONSHIP", …) to its indicator. */
export function DataClassFrom({ text }: { text?: string | null }) {
  const t = (text ?? '').toLowerCase()
  const k = (['simulated', 'missing', 'corrupted', 'observed', 'derived', 'inferred', 'reconstructed', 'unverified'] as DataKind[]).find(x => t.includes(x))
  return k ? <DataClass kind={k} /> : null
}

export function Tag({ tone, children, title }: { tone: string; children: ReactNode; title?: string }) {
  return <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-bold ring-1 ring-inset ${tone}`}>{children}</span>
}

export function PageHeader({ title, subtitle, icon: Icon, accent = '#0f4c5c', action }: { title: string; subtitle?: ReactNode; icon?: LucideIcon; accent?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        {Icon && <span className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl text-white shadow-[var(--shadow-card)]" style={{ background: accent }}><Icon className="h-5 w-5" /></span>}
        <div>
          <h1 className="text-[26px] font-bold tracking-tight text-ink">{title}</h1>
          {subtitle && <p className="max-w-4xl text-sm leading-relaxed text-muted">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

/** Banner that marks simulated or synthetic output so it is never mistaken for evidence. */
export function SimBanner({ children = 'SIMULATION — NOT EVIDENCE', note }: { children?: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border-2 border-dashed border-fuchsia-400 bg-fuchsia-50 px-4 py-2.5 text-xs text-fuchsia-950">
      <span className="shrink-0 rounded-full bg-fuchsia-700 px-2.5 py-0.5 text-[11px] font-bold tracking-wider text-white">{children}</span>
      {note && <span>{note}</span>}
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-line2 bg-soft p-6 text-center">
      <div className="text-sm font-bold uppercase tracking-wide text-ink2">{title}</div>
      {children && <div className="mx-auto mt-1 max-w-xl text-xs leading-relaxed text-muted">{children}</div>}
    </div>
  )
}

/** Label above value above bar; an N/A value always carries its reason. */
export function MiniMetric({ label, value, suffix = '', reason, onClick, na }: {
  label: ReactNode; value: number | null | undefined; suffix?: string; reason?: string; onClick?: () => void; na?: string
}) {
  const valueBar = (
    <>
      <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-ink">
        {value == null ? <span className="text-xs text-muted">{na ?? 'N/A'}</span> : `${value.toFixed(0)}${suffix}`}
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-track">
        {value != null && <div className="h-full rounded-full" style={{ width: `${Math.max(2, Math.min(100, value))}%`, background: scoreColor(value) }} />}
      </div>
    </>
  )
  // the label may contain its own glossary button, so it stays outside the clickable area
  return (
    <div className="min-w-0 px-1.5 py-1" title={onClick ? undefined : reason}>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">{label}</div>
      {onClick ? (
        <button type="button" onClick={e => { e.preventDefault(); e.stopPropagation(); onClick() }} title={reason}
          className="block w-full rounded text-left hover:bg-tealsoft focus:outline-none focus:ring-2 focus:ring-teal">{valueBar}</button>
      ) : valueBar}
    </div>
  )
}

export const STATUS_ICON = ICONS
