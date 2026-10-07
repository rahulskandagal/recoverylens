import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, FolderOpen, PlayCircle, RefreshCw, ShieldAlert, ShieldCheck, ShieldOff, Trash2 } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  SEVERITY_TONE, shieldApi, type ChangeRow, type Diff, type Incident, type RestoreResult, type SnapshotSummary, type Source,
} from '../api-shield'
import { Card, CheckIcon, DataClass, Empty, ErrorBox, Hint, PageHeader, Spinner, Stat, Tag } from '../components/ui'
import { bytes } from '../lib/format'

type Row = { rel_path: string; category: 'deleted' | 'modified' | 'corrupted'; reason?: string; size?: number }
const CAT_TONE = { deleted: 'text-red-800 bg-red-50 ring-red-300', modified: 'text-amber-900 bg-amber-50 ring-amber-300', corrupted: 'text-orange-900 bg-orange-50 ring-orange-300' }

function rowsOf(diff: Diff | null): Row[] {
  if (!diff) return []
  const mod = (c: ChangeRow[], cat: 'modified' | 'corrupted') => c.map(x => ({ rel_path: x.rel_path, category: cat, reason: x.reason }))
  return [...diff.deleted.map(d => ({ rel_path: d.rel_path, category: 'deleted' as const, size: d.size })), ...mod(diff.modified, 'modified'), ...mod(diff.corrupted, 'corrupted')]
}

export default function ShieldSource() {
  const { sid = '' } = useParams()
  const nav = useNavigate()
  const [source, setSource] = useState<Source | null>(null)
  const [snapshots, setSnapshots] = useState<SnapshotSummary[] | null>(null)
  const [diff, setDiff] = useState<Diff | null>(null)
  const [incidents, setIncidents] = useState<Incident[] | null>(null)
  const [audit, setAudit] = useState<{ ts: string; stage: string; message: string }[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [destMode, setDestMode] = useState<'recovery' | 'original' | 'custom'>('recovery')
  const [destPath, setDestPath] = useState('')
  const [confirmOverwrite, setConfirmOverwrite] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<RestoreResult | null>(null)
  const [expandedIncident, setExpandedIncident] = useState<string | null>(null)

  const load = () => {
    shieldApi.source(sid).then(setSource).catch(setErr)
    shieldApi.snapshots(sid).then(setSnapshots).catch(() => {})
    shieldApi.incidents(sid).then(setIncidents).catch(() => {})
    shieldApi.audit(sid).then(a => setAudit(a.slice(-40).reverse())).catch(() => {})
  }
  useEffect(() => { load() }, [sid]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMonitor = async () => {
    if (!source) return
    setBusy('monitor')
    try { await shieldApi.monitor(sid, source.protection !== 'active'); load() } catch (e) { setErr(e) } finally { setBusy(null) }
  }

  const createSnapshot = async () => {
    setBusy('snapshot'); setErr(null)
    try {
      const { id } = await shieldApi.createSnapshot(sid)
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 1000))
        const s = await shieldApi.snapshot(id)
        if (s.status !== 'running') break
      }
      load()
    } catch (e) { setErr(e) } finally { setBusy(null) }
  }

  const verify = async (snapId: string) => {
    setBusy(snapId)
    try { await shieldApi.verify(snapId); load() } catch (e) { setErr(e) } finally { setBusy(null) }
  }

  const scan = async () => {
    setBusy('scan'); setErr(null)
    try { const d = await shieldApi.scan(sid); setDiff(d); setSelected(new Set()); setResult(null); load() } catch (e) { setErr(e) } finally { setBusy(null) }
  }

  const toggle = (rel: string) => setSelected(s => { const n = new Set(s); n.has(rel) ? n.delete(rel) : n.add(rel); return n })

  const doRestore = async () => {
    if (!diff) return
    setBusy('restore'); setErr(null)
    try {
      const r = await shieldApi.restore(sid, diff.baseline_snapshot, [...selected], destMode, destMode === 'custom' ? destPath : null, true)
      setResult(r); setConfirming(false); setSelected(new Set()); setConfirmOverwrite(false)
    } catch (e) { setErr(e) } finally { setBusy(null) }
  }

  const removeSource = async () => {
    if (!confirm(`Remove protected source "${source?.name}"? This deletes its recovery points too (the original folder is never touched).`)) return
    await shieldApi.removeSource(sid); nav('/shield')
  }

  if (err != null && !source) return <div className="mx-auto max-w-4xl px-4 py-6"><ErrorBox error={err} /></div>
  if (!source) return <Spinner />
  const rows = rowsOf(diff)
  const hasBaseline = !!source.latest_snapshot

  return (
    <div className="mx-auto max-w-[1300px] space-y-4 px-4 py-6">
      <PageHeader icon={source.protection === 'active' ? ShieldCheck : ShieldOff} accent={source.protection === 'active' ? '#22a06b' : '#6b7c83'}
        title={source.name} subtitle={<span className="font-mono text-xs">{source.path}{!source.path_accessible && <span className="ml-2 font-sans text-danger">— path is no longer accessible from this server</span>}</span>}
        action={<div className="flex flex-wrap gap-2">
          <button onClick={toggleMonitor} disabled={busy === 'monitor'} className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-semibold ${source.protection === 'active' ? 'bg-track text-ink2 hover:bg-line' : 'bg-brand text-white hover:bg-brand-deep'} disabled:opacity-50`}>
            {source.protection === 'active' ? <ShieldOff className="h-4 w-4" /> : <ShieldCheck className="h-4 w-4" />}{source.protection === 'active' ? 'Disable protection' : 'Enable protection'}</button>
          <button onClick={createSnapshot} disabled={busy === 'snapshot'} className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-sm font-semibold text-brand-deep hover:bg-accent-2 disabled:opacity-50">
            {busy === 'snapshot' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <PlayCircle className="h-4 w-4" />}Create recovery point</button>
          <button onClick={scan} disabled={busy === 'scan' || !hasBaseline} title={hasBaseline ? '' : 'Create a recovery point first'}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">
            {busy === 'scan' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}Scan now</button>
          <button onClick={removeSource} className="inline-flex items-center gap-1.5 rounded-full bg-card px-3.5 py-1.5 text-sm font-semibold text-danger ring-1 ring-red-300 hover:bg-red-50"><Trash2 className="h-4 w-4" />Remove</button>
        </div>} />
      {err != null && <ErrorBox error={err} />}
      {source.protection === 'active' && <Hint>Continuous protection is active — the folder is watched and diffed automatically when it changes (about a 5-second worst-case delay).</Hint>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat icon={ShieldCheck} accent="#22a06b" label="Protected files" value={source.latest_snapshot?.file_count.toLocaleString() ?? 'N/A'} hint={source.latest_snapshot ? bytes(source.latest_snapshot.total_size) : 'no recovery point yet'} />
        <Stat icon={PlayCircle} accent="#3282b8" label="Recovery points" value={source.snapshot_count} hint={source.last_scan_at ? `last scan ${source.last_scan_at.slice(0, 19).replace('T', ' ')}` : 'never scanned'} />
        <Stat icon={AlertTriangle} accent={source.incident_count ? '#d9534f' : '#1fa6a6'} label="Incidents" value={source.incident_count} />
        <Stat icon={ShieldAlert} accent="#7a5bc7" label="Protection" value={source.protection.toUpperCase()} hint={source.monitoring ? 'monitoring thread running' : 'not monitoring'} />
      </div>

      <Card title="Recovery points" subtitle="Real byte-for-byte protected copies. Verify re-hashes the stored copy against its manifest." bodyClass="p-0">
        {!snapshots?.length ? <div className="p-4"><Empty title="No recovery point yet">Click "Create recovery point" above to protect the current contents of this folder.</Empty></div> : (
          <table className="w-full text-xs"><thead className="bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr><th className="px-4 py-2">ID</th><th>Created</th><th>Files</th><th>Size</th><th>Status</th><th className="pr-4 text-right">Action</th></tr></thead>
            <tbody>{snapshots.map(s => (
              <tr key={s.id} className="border-t border-line">
                <td className="px-4 py-2 font-mono">{s.id}</td><td>{s.created_at.slice(0, 19).replace('T', ' ')}</td>
                <td>{s.file_count.toLocaleString()}</td><td>{bytes(s.total_size)}</td>
                <td><Tag tone={s.status === 'verified' ? 'text-emerald-800 bg-emerald-50 ring-emerald-300' : s.status === 'protected' ? 'text-sky-900 bg-sky-50 ring-sky-300' : s.status === 'failed' || s.status === 'corrupted' ? 'text-red-800 bg-red-50 ring-red-300' : 'text-amber-900 bg-amber-50 ring-amber-300'}>{s.status.toUpperCase()}</Tag>{s.error && <div className="mt-0.5 text-danger">{s.error}</div>}</td>
                <td className="pr-4 text-right">{(s.status === 'protected' || s.status === 'verified') && <button onClick={() => verify(s.id)} disabled={busy === s.id} className="rounded-full bg-tealsoft px-2.5 py-1 text-[11px] font-semibold text-brand hover:brightness-95 disabled:opacity-50">{busy === s.id ? 'Verifying…' : 'Verify'}</button>}</td>
              </tr>))}</tbody></table>
        )}
      </Card>

      <Card title="Changes since the last recovery point" subtitle={diff ? `Scanned ${diff.scanned_at.slice(0, 19).replace('T', ' ')} against ${diff.baseline_snapshot} · ${diff.unchanged} unchanged file(s)` : 'Click "Scan now" above.'}>
        {!diff ? <Empty title="No scan yet" /> : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {([['Deleted', diff.deleted.length, '#d9534f'], ['Modified', diff.modified.length, '#f4a340'], ['Corrupted', diff.corrupted.length, '#e07b39'],
              ['New files', diff.new_files.length, '#3282b8'], ['Renamed', diff.renamed.length, '#7a5bc7']] as const).map(([l, v, c]) => (
              <div key={l} className="rounded-xl border border-line bg-soft p-3"><div className="text-[10px] font-semibold uppercase tracking-wider text-muted">{l}</div><div className="font-mono text-2xl font-bold" style={{ color: v ? c : '#8a9aa0' }}>{v}</div></div>
            ))}
          </div>
        )}
        {diff && diff.renamed.length > 0 && <ul className="mt-3 space-y-0.5 text-xs text-ink2">{diff.renamed.map(r => <li key={r.from}>{r.from} → {r.to} <span className="text-muted">(renamed; no action needed)</span></li>)}</ul>}
        {diff && diff.new_files.length > 0 && <p className="mt-2 text-xs text-muted">New, untracked file(s): {diff.new_files.map(f => f.rel_path).join(', ')}. These are not covered by Recovery Shield until the next recovery point.</p>}
      </Card>

      {diff && (
        <Card icon={FolderOpen} accent="#0f4c5c" title="Recovery Center" subtitle="Select files to restore from the protected recovery point above. Restoring never modifies files that were not selected.">
          {!rows.length ? <Empty title="Nothing to restore">No deleted, modified or corrupted files were found in the last scan.</Empty> : (
            <>
              <div className="max-h-72 overflow-y-auto rounded-xl border border-line scroll-thin">
                <table className="w-full text-xs"><thead className="sticky top-0 bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr><th className="w-8 px-3 py-2"></th><th>File</th><th>Category</th><th>Detail</th></tr></thead>
                  <tbody>{rows.map(r => (
                    <tr key={r.rel_path} className="border-t border-line hover:bg-tealsoft">
                      <td className="px-3 py-1.5"><input type="checkbox" checked={selected.has(r.rel_path)} onChange={() => toggle(r.rel_path)} className="accent-teal" /></td>
                      <td className="py-1.5 font-mono">{r.rel_path}</td>
                      <td><Tag tone={CAT_TONE[r.category]}>{r.category.toUpperCase()}</Tag></td>
                      <td className="text-ink2">{r.reason ?? (r.size != null ? bytes(r.size) : '—')}</td>
                    </tr>))}</tbody></table>
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label className="text-xs text-muted">Destination
                  <select value={destMode} onChange={e => { setDestMode(e.target.value as typeof destMode); setConfirmOverwrite(false) }} className="mt-0.5 block rounded-lg border border-line2 bg-soft px-2.5 py-1.5 text-sm text-ink">
                    <option value="recovery">Safe recovery folder (recommended)</option>
                    <option value="original">Original location (overwrites)</option>
                    <option value="custom">Custom folder…</option>
                  </select></label>
                {destMode === 'custom' && <label className="min-w-[280px] flex-1 text-xs text-muted">Folder path
                  <input value={destPath} onChange={e => setDestPath(e.target.value)} placeholder="C:\Recovery\Incident-0001" className="mt-0.5 block w-full rounded-lg border border-line2 bg-soft px-2.5 py-1.5 font-mono text-sm text-ink" /></label>}
                <button onClick={() => setConfirming(true)} disabled={!selected.size || (destMode === 'custom' && !destPath.trim())}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">Restore selected ({selected.size})</button>
              </div>
              {confirming && (
                <div className="mt-3 space-y-2 rounded-xl border-2 border-dashed border-accent bg-[#fff8ea] p-3 text-sm">
                  <div className="font-bold uppercase tracking-wide text-brand-deep">Restore confirmation</div>
                  <div>Files: <b>{selected.size}</b></div>
                  <div>Destination: <span className="font-mono">{destMode === 'recovery' ? 'a new timestamped folder under data/shield_restored/' : destMode === 'original' ? source.path + ' (overwrites existing files with the same name)' : destPath}</span></div>
                  <div className="text-ink2">Original evidence in the protected recovery point will NOT be modified.</div>
                  {destMode === 'original' && (
                    <label className="flex items-center gap-2 text-xs text-danger"><input type="checkbox" checked={confirmOverwrite} onChange={e => setConfirmOverwrite(e.target.checked)} />
                      I understand this will overwrite any current file at the original location with the same name.</label>
                  )}
                  <div className="flex gap-2 pt-1">
                    <button onClick={() => setConfirming(false)} className="rounded-full bg-track px-4 py-1.5 text-xs font-semibold text-ink2">Cancel</button>
                    <button onClick={doRestore} disabled={busy === 'restore' || (destMode === 'original' && !confirmOverwrite)} className="rounded-full bg-danger px-4 py-1.5 text-xs font-bold text-white disabled:opacity-50">{busy === 'restore' ? 'Restoring…' : 'Restore'}</button>
                  </div>
                </div>
              )}
              {result && (
                <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-xs">
                  <div className="mb-1 flex items-center gap-2 font-bold text-ink"><CheckCircle2 className="h-4 w-4 text-success" />{result.restored.length} of {result.requested} file(s) restored to <span className="font-mono">{result.destination}</span></div>
                  <ul className="space-y-0.5">{result.restored.map(r => <li key={r.rel_path} className="flex items-center gap-1.5"><CheckIcon status="pass" />{r.rel_path}<Tag tone={r.byte_identical ? 'text-emerald-800 bg-emerald-50 ring-emerald-300' : 'text-red-800 bg-red-50 ring-red-300'}>{r.byte_identical ? 'BYTE-IDENTICAL' : 'HASH MISMATCH'}</Tag></li>)}</ul>
                  {result.failed.length > 0 && <ul className="mt-1 space-y-0.5 text-red-900">{result.failed.map(f => <li key={f.rel_path}>{f.rel_path}: {f.reason}</li>)}</ul>}
                </div>
              )}
            </>
          )}
        </Card>
      )}

      <Card title="Incidents" subtitle="A new incident is raised only for newly-observed loss, not repeatedly for something already reported.">
        {!incidents?.length ? <p className="text-xs text-muted">No incidents for this source.</p> : (
          <div className="space-y-2">{incidents.map(inc => (
            <div key={inc.id} className="rounded-xl border border-line bg-soft p-3">
              <button onClick={() => setExpandedIncident(expandedIncident === inc.id ? null : inc.id)} className="flex w-full flex-wrap items-center gap-2 text-left text-xs">
                <Tag tone={SEVERITY_TONE[inc.severity]}>{inc.severity}</Tag><span className="font-mono">{inc.id}</span><span className="text-ink2">{inc.summary}</span>
                <span className="ml-auto text-muted">{inc.created_at.slice(0, 19).replace('T', ' ')}</span>
              </button>
              {expandedIncident === inc.id && (
                <div className="mt-2 grid gap-2 border-t border-line pt-2 text-[11px] sm:grid-cols-3">
                  <div><div className="font-semibold uppercase text-muted">Deleted</div><ul className="list-disc pl-4">{inc.details.deleted.map(d => <li key={d.rel_path}>{d.rel_path}</li>)}</ul></div>
                  <div><div className="font-semibold uppercase text-muted">Modified</div><ul className="list-disc pl-4">{inc.details.modified.map(d => <li key={d.rel_path}>{d.rel_path}</li>)}</ul></div>
                  <div><div className="font-semibold uppercase text-muted">Corrupted</div><ul className="list-disc pl-4">{inc.details.corrupted.map(d => <li key={d.rel_path}>{d.rel_path}{d.reason ? ` — ${d.reason}` : ''}</li>)}</ul></div>
                </div>
              )}
            </div>))}</div>
        )}
      </Card>

      <Card title="Audit log" bodyClass="p-0">
        <ol className="max-h-64 space-y-0.5 overflow-y-auto p-3 font-mono text-[11px] text-ink2 scroll-thin">
          {audit?.map((a, i) => <li key={i}>{a.ts.slice(11, 19)} <DataClass kind="observed" /> {a.message}</li>)}
          {!audit?.length && <li className="text-muted">No events yet.</li>}
        </ol>
      </Card>
    </div>
  )
}
