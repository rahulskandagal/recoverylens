import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, CircleHelp, Eye, FileStack, FolderOpen, HardDrive, Info, Lightbulb, Puzzle, ShieldCheck, Sparkles, XCircle } from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type DiskMap as DM, type FileDetail, type FileRow, type Finding, type Priority, type Status } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import DiskMap from '../components/DiskMap'
import FileDrawer from '../components/FileDrawer'
import ExportPanel from '../components/ExportPanel'
import FilePreview from '../components/FilePreview'
import Insights, { QuickActions } from '../components/Insights'
import Term from '../components/Term'
import { Card, MiniMetric, PriorityBadge, SecurityBadge, Spinner, Stat, StatusBadge } from '../components/ui'
import { PRIORITY_META, SECURITY_META, STATUS_META, TYPE_COLOR, bytes, niceType, num, plural } from '../lib/format'

const tipStyle = { background: '#ffffff', border: '1px solid #ecdfc8', borderRadius: 10, fontSize: 12, color: '#17323a', boxShadow: '0 4px 16px rgba(23,50,58,0.10)' }
const axis = { stroke: '#dccdb1', fontSize: 11, tick: { fill: '#6b7c83' } }

/** Integer ticks scaled to the data (0..max), never 0-4 for a single item. */
export function intTicks(max: number): number[] | undefined {
  if (max <= 0) return [0, 1]
  if (max <= 8) return Array.from({ length: max + 1 }, (_, i) => i)
  return undefined
}

export default function Dashboard() {
  const { c } = useOutletContext<CaseCtx>()
  const [files, setFiles] = useState<FileRow[] | null>(null)
  const [map, setMap] = useState<DM | null>(null)
  const [drawer, setDrawer] = useState<{ id: string; mode: 'integrity' | 'preview' } | null>(null)
  useEffect(() => {
    api.files(c.id).then(setFiles)
    api.diskmap(c.id).then(setMap)
  }, [c.id])
  const s = c.summary!
  if (!files) return <Spinner />
  const it = s.input_type
  const single = it?.kind === 'single_file'
  const findings: Finding[] = s.findings ?? s.key_challenges.map(t => ({ level: 'warning' as const, text: t }))
  const top = files.slice(0, 6)
  const few = files.length < 3
  const families = Object.keys(s.fragment_families).length
  const sec = s.security
  const secFlagged = sec ? (sec.counts.MALWARE_DETECTED ?? 0) + (sec.counts.SUSPICIOUS ?? 0) : 0
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight text-ink">Recovery overview</h1>
          <p className="mt-0.5 text-sm text-muted">
            {s.storage_medium} · {bytes(s.storage_size_bytes)} · {it ? <span className="text-ink2">{it.label}</span> : null} · analysed in {s.analysis_seconds}s
          </p>
        </div>
        <Link to="files" className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white shadow hover:bg-brand-deep">
          <FolderOpen className="h-4 w-4" />Browse recovered files
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat icon={HardDrive} accent="#0f4c5c" label="Scanned" value={bytes(s.storage_size_bytes)} hint={`${plural(s.total_sectors_scanned, 'sector')} · ${plural(s.total_clusters, 'cluster')}`} />
        <Stat icon={Puzzle} accent="#3282b8" label="Fragments" value={num(s.potential_fragments_identified)} hint={plural(families, 'content family', 'content families')} />
        <Stat icon={FileStack} accent="#7a5bc7" label={single ? 'Files validated' : 'Candidate files'} value={num(s.candidate_files_identified)}
          hint={single ? 'single-file input: no carving' : plural(s.directory_entries.length, 'deleted directory entry', 'deleted directory entries')} />
        <Stat icon={CheckCircle2} accent="#22a06b" label="Recovered" value={num(s.fully_recovered + s.mostly_recovered)} hint={`${s.fully_recovered} fully · ${s.mostly_recovered} mostly`} />
        <Stat icon={Lightbulb} accent="#f4a340" label="Partial" value={num(s.partially_recovered + s.fragment_only)} hint={`${s.partially_recovered} partial · ${s.fragment_only} fragment only`} />
        <Stat icon={CircleHelp} accent="#7a5bc7" label="Uncertain" value={num(s.uncertain)} hint="evidence insufficient to decide" />
        <Stat icon={XCircle} accent="#d9534f" label="Unrecoverable" value={num(s.unrecoverable)}
          hint={s.unrecoverable_sectors ? `${plural(s.unrecoverable_sectors, 'sector')} not located` : 'no declared bytes missing'} />
        <Stat icon={ShieldCheck} accent={secFlagged ? '#d9534f' : '#1fa6a6'} label="Security flags" value={num(secFlagged)}
          hint={sec ? (sec.engine ? (secFlagged ? 'suspicious / malware artifacts' : 'no detections') + (sec.simulated_engine ? ' · simulated demo engine' : '') : 'not scanned: no antivirus engine') : 'not available'} />
      </div>

      <QuickActions caseId={c.id} />

      <div className="grid gap-4 xl:grid-cols-[1.45fr_1fr]">
        <Card icon={Sparkles} accent="#f4a340" title="Review first" subtitle="Ordered by the configured priority model. Expand a row for the full reasoning; click Integrity for the checklist."
          action={<Link to="priorities" className="text-xs text-brand hover:underline">Adjust criteria</Link>}>
          <div className="divide-y divide-line">
            {top.map(f => <ReviewRow key={f.file_id} f={f} onIntegrity={() => setDrawer({ id: f.file_id, mode: 'integrity' })}
              onPreview={() => setDrawer({ id: f.file_id, mode: 'preview' })} />)}
          </div>
        </Card>
        <Card icon={Lightbulb} accent="#1fa6a6" title="Key findings">
          <ul className="space-y-2 text-sm">
            {findings.map((x, i) => (
              <li key={i} className="flex gap-2">
                {x.level === 'warning' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-label="warning" />
                  : x.level === 'success' ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-label="ok" />
                    : <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-label="info" />}
                <span className={x.level === 'warning' ? 'text-ink' : x.level === 'success' ? 'text-success' : 'text-muted'}>{x.text}</span>
              </li>))}
          </ul>
          <div className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-muted"><Term k="coverage">Recovery coverage</Term></div>
              {s.overall_recovery_coverage != null ? (
                <><div className="font-mono text-xl text-ink">{s.overall_recovery_coverage.toFixed(1)}%</div>
                  <div className="text-[11px] text-muted">{s.coverage_reason ?? 'of bytes that recovered structures say existed'}</div></>
              ) : (
                <><div className="font-mono text-sm text-ink2">N/A</div>
                  <div className="text-[11px] text-muted">{(s.coverage_reason ?? 'N/A').replace(/^N\/A – /, '')}</div></>
              )}
              {s.assigned_coverage != null && (
                <div className="mt-1 text-[11px] text-muted" title={s.assigned_coverage_reason}>Fallback: {s.assigned_coverage.toFixed(1)}% of non-empty bytes assigned to a file</div>
              )}
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider text-muted"><Term k="evidence">Evidence integrity</Term></div>
              <div className={`font-mono text-xl ${c.evidence_unchanged ? 'text-success' : 'text-amber-800'}`}>{c.evidence_unchanged ? 'Unchanged' : 'Unverified'}</div>
              <div className="truncate font-mono text-[11px] text-muted" title={c.image_sha256}>SHA-256 {c.image_sha256.slice(0, 20)}…</div>
            </div>
          </div>
          {c.evaluation && (
            <Link to="evaluation" className="mt-4 flex items-center gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/5 p-3 text-xs text-success hover:bg-emerald-500/10">
              <Sparkles className="h-4 w-4 text-success" />
              <span>Ground-truth check: placement precision <b>{c.evaluation.cluster_placement_precision ?? '—'}%</b>, link accuracy <b>{c.evaluation.edge_accuracy ?? '—'}%</b></span>
            </Link>
          )}
        </Card>
      </div>

      {sec && <SecurityPanel sec={sec} files={files} />}

      {few && files[0] && <InlinePreview caseId={c.id} fileId={files[0].file_id} />}

      {few ? <CompactSummary files={files} /> : <Charts files={files} />}

      <Insights caseId={c.id} />

      <Card title="Storage map" subtitle={map?.unit === 512 ? 'Small input: one cell per 512-byte sector. Hover for byte offsets.' : 'One cell per 4 KiB cluster. Hover for byte offsets; click a coloured cell to open its file.'}>
        {map ? <DiskMap map={map} files={files} caseId={c.id} /> : <Spinner />}
      </Card>

      {s.directory_entries.length > 0 && (
        <Card title="Recovered directory metadata" subtitle="FAT-style directory entries found in unallocated space; deleted long file names rebuilt (first character recovered via the LFN checksum).">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
                <tr><th className="py-1.5">Name</th><th>Short name</th><th>State</th><th>First cluster</th><th>Size</th><th>Modified</th><th>Name confidence</th></tr>
              </thead>
              <tbody className="font-mono">
                {s.directory_entries.map(d => (
                  <tr key={d.entry_offset} className="border-t border-line text-ink2">
                    <td className="py-1.5 font-sans text-ink">{d.name}</td><td>{d.short_name}</td>
                    <td>{d.deleted ? <span className="text-amber-800">deleted</span> : 'active'}</td>
                    <td>{d.first_cluster}</td><td>{bytes(d.size)}</td><td>{d.modified?.replace('T', ' ')}</td>
                    <td>{Math.round(d.name_confidence * 100)}%</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {drawer && <FileDrawer caseId={c.id} fileId={drawer.id} mode={drawer.mode} onClose={() => setDrawer(null)} />}
    </div>
  )
}

function ReviewRow({ f, onIntegrity, onPreview }: { f: FileRow; onIntegrity: () => void; onPreview: () => void }) {
  const [open, setOpen] = useState(false)
  const lm = f.links_metric
  return (
    <div className="py-2.5">
      <div className="grid grid-cols-1 items-center gap-3 md:grid-cols-[minmax(0,1fr)_252px]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <PriorityBadge level={f.priority_level} />
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: TYPE_COLOR[f.file_type] }} aria-hidden />
            <Link to={`files/${f.file_id}`} className="min-w-0 truncate font-medium text-ink hover:text-teal">{f.file_name}</Link>
            <StatusBadge status={f.recovery_status} />
            {f.security && <SecurityBadge status={f.security.status} test={f.security.is_test} title={f.security.explanation} />}
          </div>
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
            className="mt-1 flex w-full items-start gap-1 text-left text-xs text-muted hover:text-brand">
            <ChevronDown className={`mt-0.5 h-3.5 w-3.5 shrink-0 transition ${open ? 'rotate-180' : ''}`} />
            <span className={open ? '' : 'line-clamp-1'}>{f.status_reason ?? f.priority_reason}</span>
          </button>
        </div>
        <div className="grid grid-cols-3 gap-1">
          <MiniMetric label={<Term k="integrity">Integrity</Term>} value={f.integrity_score} onClick={onIntegrity} reason="Click for the validator checklist" />
          <MiniMetric label={<Term k="recon">Recon</Term>} value={f.reconstruction_percentage} suffix="%" reason={f.recon_confidence_reason}
            na={f.reconstruction_percentage == null ? 'N/A' : undefined} />
          <MiniMetric label={<Term k="links">Links</Term>} value={lm ? lm.value : f.relationship_confidence} suffix="%" reason={lm?.reason} />
        </div>
      </div>
      {open && (
        <div className="ml-5 mt-2 space-y-1.5 rounded-md bg-soft p-3 text-xs leading-relaxed text-ink2">
          <div><b className="text-ink">Status: </b>{f.status_reason}</div>
          <div><b className="text-ink">Recon: </b>{f.recon_confidence_reason ?? '—'}</div>
          {lm && <div><b className="text-ink">{lm.label}: </b>{lm.reason}</div>}
          <div><b className="text-ink"><Term k="priority">Priority</Term>: </b>{f.priority_reason}</div>
          <div className="flex gap-3 pt-1">
            <button onClick={onIntegrity} className="text-brand hover:underline">Integrity checklist</button>
            <button onClick={onPreview} className="inline-flex items-center gap-1 text-brand hover:underline"><Eye className="h-3.5 w-3.5" />Preview & download</button>
          </div>
        </div>
      )}
    </div>
  )
}

function InlinePreview({ caseId, fileId }: { caseId: string; fileId: string }) {
  const [f, setF] = useState<FileDetail | null>(null)
  useEffect(() => { api.file(caseId, fileId).then(setF) }, [caseId, fileId])
  return (
    <Card icon={Eye} accent="#3282b8" title="Recovered file" subtitle={f ? `${f.file_name} · ${bytes(f.size_bytes)} · preview and export use the same reconstructed bytes` : undefined}>
      {f ? <div className="grid gap-5 xl:grid-cols-[1.3fr_1fr]"><FilePreview f={f} caseId={caseId} maxHeight={360} /><ExportPanel f={f} caseId={caseId} /></div> : <Spinner />}
    </Card>
  )
}

function CompactSummary({ files }: { files: FileRow[] }) {
  return (
    <Card title="Summary" subtitle={`${plural(files.length, 'file')}: too few for distribution charts`}>
      <table className="w-full text-sm">
        <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
          <tr><th className="py-1">File</th><th>Type</th><th>Status</th><th className="text-right"><Term k="integrity">Integrity</Term></th><th className="text-right"><Term k="priority">Priority</Term></th></tr>
        </thead>
        <tbody>
          {files.map(f => (
            <tr key={f.file_id} className="border-t border-line">
              <td className="py-1.5 pr-2 text-ink">{f.file_name}</td>
              <td className="text-muted">{niceType(f.file_type)}</td>
              <td><StatusBadge status={f.recovery_status} /></td>
              <td className="text-right font-mono text-ink">{f.integrity_score.toFixed(0)}/100</td>
              <td className="text-right"><PriorityBadge level={f.priority_level} /></td>
            </tr>))}
        </tbody>
      </table>
    </Card>
  )
}

function Charts({ files }: { files: FileRow[] }) {
  const byType = Object.entries(files.reduce<Record<string, number>>((a, f) => ({ ...a, [f.file_type]: (a[f.file_type] ?? 0) + 1 }), {}))
    .map(([t, n]) => ({ t, label: niceType(t), n })).sort((a, b) => b.n - a.n)
  const statuses = (Object.keys(STATUS_META) as Status[]).map(k => ({ k, label: STATUS_META[k].label, n: files.filter(f => f.recovery_status === k).length }))
  const buckets = [0, 20, 40, 60, 80].map(lo => ({ label: `${lo}–${lo + 20}`, n: files.filter(f => f.integrity_score >= lo && (lo === 80 ? f.integrity_score <= 100 : f.integrity_score < lo + 20)).length }))
  const prios = (Object.keys(PRIORITY_META) as Priority[]).map(k => ({ k, n: files.filter(f => f.priority_level === k).length }))
  const tMax = Math.max(...byType.map(d => d.n)), bMax = Math.max(...buckets.map(d => d.n))
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card title="File types" subtitle="Candidates per technical type">
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={byType} margin={{ left: -24, right: 4, top: 8 }}>
            <CartesianGrid vertical={false} stroke="#f0e6d3" />
            <XAxis dataKey="label" {...axis} />
            <YAxis allowDecimals={false} domain={[0, Math.max(1, tMax)]} ticks={intTicks(tMax)} {...axis} />
            <Tooltip contentStyle={tipStyle} cursor={{ fill: 'rgba(31,166,166,0.08)' }} formatter={(v) => [v, 'candidates']} />
            <Bar dataKey="n" radius={[4, 4, 0, 0]} maxBarSize={28}>{byType.map(d => <Cell key={d.t} fill={TYPE_COLOR[d.t] ?? '#64748b'} />)}</Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card title="Recovery status">
        <div className="space-y-2">{statuses.map(st => (
          <div key={st.k} className="grid grid-cols-[130px_1fr_24px] items-center gap-2 text-xs">
            <span className="text-ink2"><span aria-hidden className="mr-1">{STATUS_META[st.k].icon}</span>{st.label}</span>
            <div className="h-2 rounded-full bg-track"><div className="h-full rounded-full" style={{ width: `${(st.n / Math.max(1, files.length)) * 100}%`, background: STATUS_META[st.k].color }} /></div>
            <span className="text-right font-mono text-ink2">{st.n}</span>
          </div>))}</div>
      </Card>
      <Card title="Integrity distribution" subtitle="Candidates per integrity band">
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={buckets} margin={{ left: -24, right: 4, top: 8 }}>
            <CartesianGrid vertical={false} stroke="#f0e6d3" />
            <XAxis dataKey="label" {...axis} />
            <YAxis allowDecimals={false} domain={[0, Math.max(1, bMax)]} ticks={intTicks(bMax)} {...axis} />
            <Tooltip contentStyle={tipStyle} cursor={{ fill: 'rgba(31,166,166,0.08)' }} formatter={(v) => [v, 'candidates']} />
            <Bar dataKey="n" radius={[4, 4, 0, 0]} maxBarSize={32}>{buckets.map((b, i) => <Cell key={b.label} fill={['#b9e3e1', '#7fcdca', '#1fa6a6', '#157f86', '#0f4c5c'][i]} />)}</Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>
      <Card title="Priority distribution">
        <div className="space-y-2">{prios.map(p => (
          <div key={p.k} className="grid grid-cols-[110px_1fr_24px] items-center gap-2 text-xs">
            <PriorityBadge level={p.k} />
            <div className="h-2 rounded-full bg-track"><div className="h-full rounded-full" style={{ width: `${(p.n / Math.max(1, files.length)) * 100}%`, background: PRIORITY_META[p.k].color }} /></div>
            <span className="text-right font-mono text-ink2">{p.n}</span>
          </div>))}</div>
        <p className="mt-3 text-[11px] text-muted">Priority = "review earlier under current criteria", not proven importance.</p>
      </Card>
    </div>
  )
}

function SecurityPanel({ sec, files }: { sec: NonNullable<import('../api').Summary['security']>; files: FileRow[] }) {
  const flagged = files.filter(f => f.security && ['MALWARE_DETECTED', 'SUSPICIOUS', 'SCAN_FAILED'].includes(f.security.status))
  return (
    <Card icon={ShieldCheck} accent={flagged.length ? '#d9534f' : '#22a06b'} title="Security analysis"
      subtitle={`Engine: ${sec.engine ?? 'none available (ClamAV not installed)'} · Rules: ${sec.rule_engine}. Recovered artifacts are scanned as data and are never executed or opened automatically.`}>
      <div className="flex flex-wrap gap-2">
        {(Object.keys(SECURITY_META) as (keyof typeof SECURITY_META)[]).map(k => (
          <div key={k} className="flex items-center gap-2 rounded-xl border border-line bg-soft px-3 py-2">
            <SecurityBadge status={k} />
            <span className="font-mono text-lg font-bold text-ink">{sec.counts[k] ?? 0}</span>
          </div>))}
        <div className="flex items-center gap-2 rounded-xl border border-line bg-soft px-3 py-2 text-xs text-muted">
          Unassigned fragments swept: <b className="font-mono text-ink">{sec.unassigned_fragments_scanned.toLocaleString()}</b> · flagged <b className="font-mono text-ink">{sec.unassigned_fragments_flagged}</b>
        </div>
      </div>
      {flagged.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm">
          {flagged.map(f => (
            <li key={f.file_id} className="flex flex-wrap items-center gap-2">
              <SecurityBadge status={f.security!.status} test={f.security!.is_test} />
              <Link to={`files/${f.file_id}`} className="font-medium text-brand hover:underline">{f.file_name}</Link>
              <span className="text-xs text-muted">{f.security!.explanation}</span>
            </li>))}
        </ul>
      )}
      {sec.simulated_engine && <p className="mt-3 text-[11px] text-amber-900">DEMO MODE: detections come from a simulated engine that only recognises harmless test signatures. It is not an antivirus product.</p>}
      {!sec.engine && <p className="mt-3 text-[11px] text-muted">Install ClamAV (clamd on port 3310 or clamscan on PATH) to enable signature scanning. Without it, artifacts are reported as NOT SCANNED, never as clean.</p>}
    </Card>
  )
}
