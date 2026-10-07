import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Compass, Minus, Plus } from 'lucide-react'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { adv, LEVEL_META, type CasePossibility, type FilePossibility, type Region } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import Heatmap from '../components/Heatmap'
import SimulatorPanel from '../components/SimulatorPanel'
import { Card, DataClass, Empty, ErrorBox, Hint, PageHeader, Spinner, Tag } from '../components/ui'
import { bytes, niceType } from '../lib/format'

export function LevelBadge({ level }: { level: string }) {
  const m = LEVEL_META[level] ?? LEVEL_META.UNKNOWN
  return <Tag tone={m.tone}>{m.text}</Tag>
}

const SEG_COLOR: Record<string, string> = { recovered: '#22a06b', missing: '#d9534f', corrupted: '#e07b39' }

function LogicalBar({ p, pick, sel }: { p: FilePossibility; pick: (r: Region) => void; sel: Region | null }) {
  return (
    <div className="flex h-7 w-full overflow-hidden rounded-md bg-track">
      {p.segments.map((s, i) => {
        const region = p.regions.find(r => r.start === s.start && r.kind === s.kind)
        return (
          <button key={i} disabled={!region} onClick={() => region && pick(region)}
            title={`${s.kind} ${s.start.toLocaleString()}–${s.end.toLocaleString()} (${bytes(s.length)})${s.fragment_id ? ` · ${s.fragment_id}` : ''}`}
            className={`h-full border-r border-white/70 ${region ? 'cursor-pointer hover:brightness-110' : 'cursor-default'} ${sel && region === sel ? 'ring-2 ring-inset ring-ink' : ''}`}
            style={{ width: `${Math.max(0.5, (100 * s.length) / Math.max(1, p.size))}%`, background: SEG_COLOR[s.kind] ?? '#8a9aa0',
              backgroundImage: s.kind === 'missing' ? 'repeating-linear-gradient(45deg, rgba(255,255,255,.35) 0 4px, transparent 4px 8px)' : undefined }} />
        )
      })}
    </div>
  )
}

function RegionDetail({ caseId, p, r }: { caseId: string; p: FilePossibility; r: Region }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="space-y-3 rounded-2xl border border-line bg-soft p-4">
      <div className="flex flex-wrap items-center gap-2">
        <DataClass kind={r.kind === 'missing' ? 'missing' : 'corrupted'} />
        <b className="text-sm text-ink">{r.kind === 'missing' ? 'Missing' : 'Corrupted'} region #{r.index}</b>
        <LevelBadge level={r.level} />
      </div>
      <div className="grid gap-2 text-xs sm:grid-cols-3">
        <div><div className="text-[10px] font-semibold uppercase text-muted">Offset (logical)</div><div className="font-mono">{r.offset_hex} ({r.start.toLocaleString()})</div></div>
        <div><div className="text-[10px] font-semibold uppercase text-muted">Size</div><div className="font-mono">{r.length.toLocaleString()} B</div></div>
        <div><div className="text-[10px] font-semibold uppercase text-muted">Expected content</div><div>{r.expected_content}</div></div>
      </div>
      <div className="text-xs"><div className="text-[10px] font-semibold uppercase text-muted">Known evidence</div>
        {r.known_evidence.length ? <ul className="list-disc pl-4 text-ink2">{r.known_evidence.map(e => <li key={e}>{e}</li>)}</ul> : <span className="text-muted">none</span>}</div>
      <div className="text-xs"><div className="text-[10px] font-semibold uppercase text-muted">Reason for assessment</div><div className="text-ink">{r.reason}</div></div>
      <div>
        <div className="text-[10px] font-semibold uppercase text-muted">Candidate fragments</div>
        {r.candidates.length ? (
          <div className="mt-1 overflow-x-auto"><table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase text-muted"><tr><th className="py-1">Fragment</th><th>Strength</th><th>Evidence</th><th>Conflicts</th></tr></thead>
            <tbody>{r.candidates.map(c => (
              <tr key={c.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-2 font-mono"><Link to={`../dna?frag=${c.id}`} className="text-brand hover:underline">{c.id}</Link><div className="text-[10px] text-muted">{c.offset_hex} · {bytes(c.length)}</div></td>
                <td className="pr-2"><Tag tone={c.strength === 'structural' ? 'text-emerald-800 bg-emerald-50 ring-emerald-300' : 'text-sky-900 bg-sky-50 ring-sky-300'}>{c.strength}</Tag></td>
                <td className="pr-2 text-ink2">{c.evidence.join('; ')}</td>
                <td className="text-red-800">{c.conflicts.join('; ') || '—'}</td>
              </tr>))}</tbody></table></div>
        ) : <p className="text-xs text-muted">No compatible unassigned fragment exists in this image.</p>}
      </div>
      {r.kind === 'missing' && <SimulatorPanel key={`${p.file_id}-${r.index}`} caseId={caseId} fileId={p.file_id} region={r} />}
    </motion.div>
  )
}

export default function RecoveryPossibility() {
  const { c } = useOutletContext<CaseCtx>()
  const [d, setD] = useState<CasePossibility | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [sp, setSp] = useSearchParams()
  const [sel, setSel] = useState<{ fid: string; r: Region } | null>(null)
  useEffect(() => { adv.possibility(c.id).then(setD).catch(setErr) }, [c.id])
  if (err) return <ErrorBox error={err} />
  if (!d) return <Spinner />
  const focus = sp.get('file')
  const withRegions = d.files.filter(f => f.regions.length || f.level === 'UNKNOWN')
  const shown = focus ? d.files.filter(f => f.file_id === focus) : withRegions
  return (
    <div className="space-y-4">
      <PageHeader icon={Compass} accent="#f4a340" title="Recovery Possibility Map"
        subtitle="Is further recovery realistically possible? Each level comes from explicit evidence rules (structure-declared gaps, compatible unassigned fragments, conflicts), never from an arbitrary score. Missing data is never claimed recoverable without supporting evidence." />
      <div className="flex flex-wrap gap-2">
        {['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN', 'N/A'].map(l => (
          <div key={l} className="rounded-2xl border border-line bg-card px-4 py-2 shadow-[var(--shadow-card)]">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">{l === 'N/A' ? 'Complete' : l}</div>
            <div className="font-mono text-2xl font-bold" style={{ color: LEVEL_META[l].color }}>{d.counts[l] ?? 0}</div>
          </div>
        ))}
      </div>
      <Card title="Storage heatmap" subtitle="Where recovered bytes, candidate fragments, uncertain and corrupted data lie on the storage image. Click a coloured cell to focus its artifact.">
        <Heatmap h={d.heatmap} onPick={o => { if (d.files.some(f => f.file_id === o)) { const n = new URLSearchParams(sp); n.set('file', o); setSp(n) } }} />
      </Card>
      <details className="rounded-2xl border border-line bg-card p-4 text-xs text-ink2 shadow-[var(--shadow-card)]">
        <summary className="cursor-pointer font-semibold text-ink">How levels are decided</summary>
        <pre className="mt-2 whitespace-pre-wrap font-sans">{d.rules}</pre>
      </details>
      {focus && <button onClick={() => { const n = new URLSearchParams(sp); n.delete('file'); setSp(n) }} className="text-xs font-semibold text-brand hover:underline">← Show all incomplete artifacts</button>}
      {!shown.length && <Empty title="Nothing left to recover">Every artifact is complete: no missing or corrupted ranges were found.</Empty>}
      {shown.map(p => (
        <Card key={p.file_id} title={<span className="flex flex-wrap items-center gap-2">RECOVERY POSSIBILITY: <LevelBadge level={p.level} /> <span className="font-normal text-muted">{p.file_name}</span></span>}
          subtitle={`${niceType(p.file_type)} · ${p.status.replaceAll('_', ' ').toLowerCase()} · ${p.data_class}`}>
          <div className="grid gap-3 md:grid-cols-2">
            <ul className="space-y-1 text-xs">{p.plus.map(x => <li key={x} className="flex gap-1.5 text-emerald-900"><Plus className="mt-0.5 h-3.5 w-3.5 shrink-0" />{x}</li>)}</ul>
            <ul className="space-y-1 text-xs">{p.minus.map(x => <li key={x} className="flex gap-1.5 text-red-900"><Minus className="mt-0.5 h-3.5 w-3.5 shrink-0" />{x}</li>)}</ul>
          </div>
          {p.segments.length > 0 && <div className="mt-3"><LogicalBar p={p} sel={sel?.fid === p.file_id ? sel.r : null} pick={r => setSel({ fid: p.file_id, r })} />
            <p className="mt-1 text-[11px] text-muted">Logical layout of the file: green recovered · orange corrupted · red hatched missing. Click a missing/corrupted region for details.</p></div>}
          {sel?.fid === p.file_id && <div className="mt-3"><RegionDetail caseId={c.id} p={p} r={sel.r} /></div>}
        </Card>
      ))}
      <Hint>Levels: HIGH = a structurally matching candidate exists · MEDIUM = family-compatible candidates only · LOW = no compatible data remains (likely overwritten) · UNKNOWN = the format does not declare what is missing.</Hint>
    </div>
  )
}
