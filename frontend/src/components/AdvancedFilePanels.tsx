import { useEffect, useState } from 'react'
import { Compass, Dna, ListTree, Scale, Workflow } from 'lucide-react'
import { Link } from 'react-router-dom'
import { adv, type ConfidenceProfile, type Provenance } from '../api-advanced'
import { Card, DataClass } from './ui'
import { scoreColor } from '../lib/format'
import { LEVEL_META } from '../api-advanced'

export function AdvancedLinks({ fid, firstFragment }: { fid: string; firstFragment?: string }) {
  const links: [string, string, typeof Workflow][] = [
    [`../twin?file=${fid}`, 'Recovery Digital Twin', Workflow], [`../structure?file=${fid}`, 'Structure Explorer', ListTree],
    [`../possibility?file=${fid}`, 'Recovery Possibility & Simulator', Compass],
    ...(firstFragment ? [[`../dna?frag=${firstFragment}`, 'Fragment DNA', Dna] as [string, string, typeof Workflow]] : []),
  ]
  return (
    <div className="flex flex-wrap gap-2">
      {links.map(([to, l, I]) => (
        <Link key={l} to={to} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1.5 text-xs font-semibold text-ink2 shadow-[var(--shadow-card)] hover:border-teal hover:text-brand">
          <I className="h-3.5 w-3.5" />{l}
        </Link>
      ))}
    </div>
  )
}

/** Six separate measures, each with the evidence that produced it (Confidence Calibration Engine). */
export function ConfidencePanel({ caseId, fid }: { caseId: string; fid: string }) {
  const [p, setP] = useState<ConfidenceProfile | null>(null)
  useEffect(() => { adv.confidence(caseId, fid).then(setP).catch(() => setP(null)) }, [caseId, fid])
  if (!p) return null
  return (
    <Card icon={Scale} accent="#0f4c5c" title="Confidence profile" subtitle={<>{p.disclaimer} <Link to="/calibration" className="text-brand hover:underline">Calibration →</Link></>}>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {p.measures.map(m => {
          const num = typeof m.value === 'number' ? m.value : null
          const color = num != null ? scoreColor(num) : LEVEL_META[String(m.value)]?.color ?? '#8a9aa0'
          return (
            <details key={m.key} className="group rounded-xl border border-line bg-soft p-3">
              <summary className="cursor-pointer list-none">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">{m.name}</span>
                  <span className="font-mono text-lg font-bold" style={{ color }}>{m.value == null ? 'N/A' : num != null ? `${num}${m.unit === '%' ? '%' : ''}` : String(m.value)}</span>
                </div>
                {num != null && <div className="mt-1 h-1.5 rounded-full bg-track"><div className="h-full rounded-full" style={{ width: `${Math.min(100, num)}%`, background: color }} /></div>}
                <div className="mt-1 text-[11px] text-muted">{m.meaning} <span className="text-brand group-open:hidden">· evidence ▸</span></div>
              </summary>
              <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-ink2">{m.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
              <p className="mt-1 text-[10px] text-muted">Basis: {m.basis}</p>
            </details>
          )
        })}
      </div>
    </Card>
  )
}

/** Extra provenance: user-approved investigator operations and simulations for this artifact. */
export function ProvenanceExtras({ caseId, fid }: { caseId: string; fid: string }) {
  const [p, setP] = useState<Provenance | null>(null)
  useEffect(() => { adv.provenance(caseId, fid).then(setP).catch(() => setP(null)) }, [caseId, fid])
  if (!p) return null
  return (
    <div className="mt-4 grid gap-4 border-t border-line pt-4 text-xs md:grid-cols-2">
      <div>
        <div className="mb-1 flex items-center gap-2 font-semibold uppercase tracking-wider text-muted">Source fragments & offsets <DataClass kind="observed" /></div>
        <table className="w-full"><tbody>{p.fragments.map((x, i) => (
          <tr key={i} className="border-t border-line"><td className="py-1 font-mono">{x.id ?? '—'}</td><td className="font-mono">{x.source_offset ?? '—'}</td>
            <td>{x.logical}</td><td>{x.state === 'missing' ? <DataClass kind="missing" /> : x.state === 'corrupted' ? <DataClass kind="corrupted" /> : null}</td></tr>))}</tbody></table>
        <p className="mt-1 text-muted">AI planner: {p.ai_planner}</p>
      </div>
      <div className="space-y-2">
        <div>
          <div className="font-semibold uppercase tracking-wider text-muted">User-approved operations</div>
          {p.user_approved_operations.length ? <ul className="mt-1 space-y-1">{p.user_approved_operations.map(o => (
            <li key={o.id} className="rounded-lg bg-soft p-2"><b>{o.id}</b> {o.title}<div className="text-muted">{o.decided} · {o.result}</div></li>))}</ul>
            : <p className="text-muted">None.</p>}
        </div>
        <div>
          <div className="flex items-center gap-2 font-semibold uppercase tracking-wider text-muted">Simulations <DataClass kind="simulated" /></div>
          {p.simulations.length ? <ul className="mt-1 space-y-0.5">{p.simulations.slice(-6).map((s, i) => <li key={i} className="text-ink2"><span className="font-mono text-muted">{s.ts.slice(11, 19)}</span> {s.message}</li>)}</ul>
            : <p className="text-muted">None run. Simulations never alter this artifact.</p>}
        </div>
      </div>
    </div>
  )
}
