import { useEffect, useMemo, useState } from 'react'
import { ArrowDownUp, Download, Eye, FileSearch, Network, Puzzle, Search } from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { api, type FileRow, type Priority, type Status } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import FileDrawer from '../components/FileDrawer'
import Term from '../components/Term'
import { Card, PriorityBadge, ScoreBar, SecurityBadge, Spinner, StatusBadge } from '../components/ui'
import { PRIORITY_META, SECURITY_META, STATUS_META, TYPE_COLOR, bytes, niceType } from '../lib/format'

type SortKey = 'priority_score' | 'file_name' | 'size_bytes' | 'integrity_score' | 'reconstruction_percentage' | 'relationship_confidence'

export default function Files() {
  const { c } = useOutletContext<CaseCtx>()
  const [all, setAll] = useState<FileRow[] | null>(null)
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [prio, setPrio] = useState('')
  const [minI, setMinI] = useState(0)
  const [minC, setMinC] = useState(0)
  const [minR, setMinR] = useState(0)
  const [sort, setSort] = useState<SortKey>('priority_score')
  const [asc, setAsc] = useState(false)
  const [serverHits, setServerHits] = useState<Set<string> | null>(null)
  const [drawer, setDrawer] = useState<{ id: string; mode: 'integrity' | 'preview' | 'export' } | null>(null)
  const [secF, setSecF] = useState('')
  useEffect(() => { api.files(c.id).then(setAll) }, [c.id])
  // keyword search also runs server-side over extracted text (PDF pages, DOCX body, logs, DB rows)
  useEffect(() => {
    if (!q.trim()) { setServerHits(null); return }
    const t = setTimeout(() => api.files(c.id, { q }).then(r => setServerHits(new Set(r.map(x => x.file_id)))), 250)
    return () => clearTimeout(t)
  }, [q, c.id])
  const rows = useMemo(() => {
    if (!all) return []
    let r = all.filter(f =>
      (!type || f.file_type === type) && (!status || f.recovery_status === status) && (!prio || f.priority_level === prio) &&
      f.integrity_score >= minI && f.relationship_confidence >= minC && (minR === 0 || (f.reconstruction_percentage ?? 0) >= minR) &&
      (!secF || f.security?.status === secF) && (!serverHits || serverHits.has(f.file_id)))
    r = [...r].sort((a, b) => {
      const av = a[sort] ?? -1, bv = b[sort] ?? -1
      const d = typeof av === 'string' ? String(av).localeCompare(String(bv)) : (av as number) - (bv as number)
      return asc ? d : -d
    })
    return r
  }, [all, type, status, prio, minI, minC, minR, sort, asc, serverHits, secF])
  if (!all) return <Spinner />
  const types = [...new Set(all.map(f => f.file_type))]
  const th = (k: SortKey, label: string, cls = '') => (
    <th className={`py-2 pr-3 font-medium ${cls}`}>
      <button onClick={() => { if (sort === k) setAsc(!asc); else { setSort(k); setAsc(k === 'file_name') } }} className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-brand ${sort === k ? 'text-brand' : ''}`}>
        {label}<ArrowDownUp className="h-3 w-3" />
      </button>
    </th>
  )
  const sel = 'rounded-lg border border-line2 bg-soft px-2 py-1.5 text-xs text-ink outline-none focus:border-teal'
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-ink">Recovered files</h1>
        <p className="text-sm text-muted">{rows.length} of {all.length} recovered candidates. <Term k="integrity">Integrity</Term>, <Term k="recon">Recon</Term> and <Term k="links">Links</Term> are separate measures; click an integrity bar for its checklist.</p>
      </div>
      <Card>
        <div className="flex flex-wrap items-end gap-2">
          <label className="relative min-w-[240px] flex-1">
            <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search filename, fragment ID (F0123) or keyword in recovered text…"
              className="w-full rounded-lg border border-line2 bg-soft py-1.5 pl-8 pr-3 text-sm text-ink outline-none focus:border-teal" />
          </label>
          <select value={type} onChange={e => setType(e.target.value)} className={sel}><option value="">All types</option>{types.map(t => <option key={t} value={t}>{niceType(t)}</option>)}</select>
          <select value={status} onChange={e => setStatus(e.target.value)} className={sel}><option value="">All statuses</option>{(Object.keys(STATUS_META) as Status[]).map(s => <option key={s} value={s}>{STATUS_META[s].label}</option>)}</select>
          <select value={secF} onChange={e => setSecF(e.target.value)} className={sel}><option value="">All security results</option>{Object.entries(SECURITY_META).map(([k, m]) => <option key={k} value={k}>{m.short}</option>)}</select>
          <select value={prio} onChange={e => setPrio(e.target.value)} className={sel}><option value="">All priorities</option>{(Object.keys(PRIORITY_META) as Priority[]).map(s => <option key={s}>{s}</option>)}</select>
          {[['Integrity ≥', minI, setMinI], ['Confidence ≥', minC, setMinC], ['Recon. ≥', minR, setMinR]].map(([l, v, set]) => (
            <label key={l as string} className="text-[11px] text-muted">{l as string} <span className="font-mono text-ink2">{v as number}</span>
              <input type="range" min={0} max={100} step={5} value={v as number} onChange={e => (set as (n: number) => void)(+e.target.value)} className="block w-24 accent-teal" />
            </label>
          ))}
        </div>
      </Card>
      <Card className="overflow-hidden" bodyClass="p-0">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[1380px] text-sm">
            <thead className="border-b border-line bg-[#f7efe0] text-left text-[11px] font-semibold uppercase tracking-wider text-ink2">
              <tr>
                {th('priority_score', 'Priority', 'pl-5 py-3')}{th('file_name', 'File')}<th className="pr-3">Type</th>{th('size_bytes', 'Size')}
                {th('integrity_score', 'Integrity')}{th('reconstruction_percentage', 'Reconstruction')}{th('relationship_confidence', 'Confidence')}
                <th className="pr-3">Security</th><th className="pr-3">Status</th><th className="pr-3" title="fragments · accepted fragment links · gaps">Relationships</th><th className="pr-5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(f => (
                <tr key={f.file_id} className={`border-b border-line hover:bg-soft ${f.security?.status === 'MALWARE_DETECTED' ? 'bg-red-50/60' : ''}`}>
                  <td className="py-3 pl-5 pr-3"><PriorityBadge level={f.priority_level} /><div className="mt-0.5 font-mono text-[10px] text-muted/70">{f.priority_score.toFixed(1)}</div></td>
                  <td className="max-w-[280px] pr-3">
                    <Link to={f.file_id} className="block truncate font-medium text-ink hover:text-teal">{f.file_name}</Link>
                    <div className="truncate text-[11px] text-muted" title={f.name_source}>{f.file_id} · {f.orphan ? 'orphan fragments' : f.name_source}</div>
                  </td>
                  <td className="pr-3"><span className="inline-flex items-center gap-1.5 text-xs"><span className="h-2 w-2 rounded-sm" style={{ background: TYPE_COLOR[f.file_type] }} />{niceType(f.file_type)}</span></td>
                  <td className="pr-3 font-mono text-xs text-muted">{bytes(f.size_bytes)}</td>
                  <td className="pr-3"><button onClick={() => setDrawer({ id: f.file_id, mode: 'integrity' })} className="w-full text-left" title="Open the integrity checklist"><ScoreBar value={f.integrity_score} /></button></td>
                  <td className="pr-3" title={f.recon_confidence_reason}>{f.reconstruction_percentage == null
                    ? <span className="text-[11px] text-muted">N/A <span className="text-muted/70">(hover)</span></span> : <ScoreBar value={f.reconstruction_percentage} suffix="%" />}</td>
                  <td className="pr-3" title={f.links_metric?.reason}>{(f.links_metric ? f.links_metric.value : f.relationship_confidence) == null
                    ? <span className="text-[11px] text-muted">N/A <span className="text-muted/70">(hover)</span></span>
                    : <ScoreBar value={f.links_metric ? f.links_metric.value : f.relationship_confidence} suffix="%" />}</td>
                  <td className="pr-3"><SecurityBadge status={f.security?.status} test={f.security?.is_test} title={f.security?.explanation} /></td>
                  <td className="pr-3"><StatusBadge status={f.recovery_status} /></td>
                  <td className="pr-3 font-mono text-xs text-muted">{f.fragment_count} frag · {f.relationships} links{f.missing_ranges ? <span className="text-danger"> · {f.missing_ranges} gap</span> : null}</td>
                  <td className="pr-5">
                    <div className="flex justify-end gap-1">
                      <Act onClick={() => setDrawer({ id: f.file_id, mode: 'preview' })} icon={Eye} label="View" />
                      <ActLink to={f.file_id} icon={FileSearch} label="Analyze" />
                      <ActLink to={`../fragments?q=${f.file_id}`} icon={Puzzle} label="Fragments" />
                      <ActLink to={`../graph?focus=${f.file_id}`} icon={Network} label="Relationships" />
                      <Act onClick={() => setDrawer({ id: f.file_id, mode: 'export' })} icon={Download}
                        label={f.export?.class === 'forensic_artifact' ? 'Artifact' : 'Export'} danger={f.security?.status === 'MALWARE_DETECTED'}
                        title={f.export ? `${f.export.label}${f.export.warning ? ' – ' + f.export.warning : ''}` : 'Export'} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p className="p-6 text-center text-sm text-muted">No candidates match these filters.</p>}
        </div>
      </Card>
      {drawer && <FileDrawer caseId={c.id} fileId={drawer.id} mode={drawer.mode} onClose={() => setDrawer(null)} />}
    </div>
  )
}

const actCls = 'inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] font-medium transition'

function ActLink({ to, icon: I, label }: { to: string; icon: typeof Eye; label: string }) {
  return <Link to={to} className={`${actCls} border-line bg-card text-ink2 hover:border-teal hover:text-brand`}><I className="h-3.5 w-3.5" />{label}</Link>
}

function Act({ onClick, icon: I, label, title, danger }: { onClick: () => void; icon: typeof Eye; label: string; title?: string; danger?: boolean }) {
  return (
    <button type="button" onClick={onClick} title={title ?? label}
      className={`${actCls} ${danger ? 'border-red-300 bg-red-50 text-red-800 hover:bg-red-100' : 'border-line bg-card text-ink2 hover:border-teal hover:text-brand'}`}>
      <I className="h-3.5 w-3.5" />{label}
    </button>
  )
}
