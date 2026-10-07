import { useEffect, useState } from 'react'
import { Lock, RefreshCw, ShieldAlert, ShieldCheck, ShieldHalf, ShieldX } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { adv, GUARD_META, type Guardian, type GuardianItem } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, ErrorBox, Hint, PageHeader, Spinner, Tag } from '../components/ui'
import { niceType } from '../lib/format'

const ICON = { 'NO DETECTION': ShieldCheck, SUSPICIOUS: ShieldAlert, 'MALWARE DETECTED': ShieldX, 'SCAN UNAVAILABLE': ShieldHalf } as const

function Item({ it }: { it: GuardianItem }) {
  const [open, setOpen] = useState(it.raw_status === 'MALWARE_DETECTED' || it.raw_status === 'SUSPICIOUS')
  const m = GUARD_META[it.status] ?? GUARD_META['SCAN UNAVAILABLE']
  const I = ICON[it.status as keyof typeof ICON] ?? ShieldHalf
  return (
    <div className={`rounded-2xl border bg-card shadow-[var(--shadow-card)] ${it.raw_status === 'MALWARE_DETECTED' ? 'border-red-300' : 'border-line'}`}>
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left">
        <span className="grid h-9 w-9 place-items-center rounded-xl" style={{ background: `${m.color}1f`, color: m.color }}><I className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-ink">{it.file_name}</div>
          <div className="text-[11px] text-muted">{it.file_id} · {niceType(it.file_type)} · SHA-256 {it.sha256?.slice(0, 16)}…</div>
        </div>
        <Tag tone={m.tone}>{it.status}{it.is_test ? ' (SIMULATED / TEST)' : ''}</Tag>
        {it.isolation && <Tag tone="text-violet-800 bg-violet-50 ring-violet-300"><Lock className="h-3 w-3" />ISOLATED</Tag>}
      </button>
      {open && (
        <div className="space-y-3 border-t border-line px-4 py-3 text-xs">
          <div className="font-bold uppercase tracking-wide text-ink">{it.headline}</div>
          {it.explanation && <p className="text-ink2">{it.explanation}</p>}
          {it.absence_note && <p className="rounded-lg bg-soft px-2.5 py-1.5 text-ink2"><b>Note:</b> {it.absence_note}</p>}
          {it.detections.length > 0 && (
            <div className="overflow-x-auto"><table className="w-full min-w-[760px]">
              <thead className="text-left text-[10px] uppercase tracking-wider text-muted"><tr><th className="py-1">Detection</th><th>Scanner</th><th>Rule / signature</th><th>Evidence</th><th>File region</th><th>Scan time</th><th>Scanner status</th></tr></thead>
              <tbody>{it.detections.map((d, i) => (
                <tr key={i} className="border-t border-line align-top">
                  <td className="py-1.5 pr-2 font-semibold text-ink">{d.name}{d.simulated ? ' (SIMULATED)' : ''}</td>
                  <td className="pr-2 text-ink2">{d.scanner}</td><td className="pr-2 font-mono text-[11px]">{d.rule ?? '—'}</td>
                  <td className="pr-2 text-ink2">{d.evidence}</td><td className="pr-2 font-mono text-[11px]">{d.region ?? 'n/a'}</td>
                  <td className="pr-2 font-mono text-[11px]">{d.timestamp?.slice(0, 19).replace('T', ' ')}</td><td>{d.scanner_status}</td>
                </tr>))}</tbody></table></div>
          )}
          <div className="grid gap-2 sm:grid-cols-3">
            <div><span className="text-muted">Antivirus engine:</span> {it.scanner ?? 'none'} {it.scanner_version && <span className="text-muted">({it.scanner_version})</span>} – {it.engine_status}</div>
            <div><span className="text-muted">Rule engine:</span> {it.rule_engine}</div>
            <div><span className="text-muted">Export gate:</span> {it.export_gate}</div>
          </div>
          {it.isolation && <p className="text-violet-900"><Lock className="mr-1 inline h-3.5 w-3.5" />Isolated copy <span className="font-mono">{it.isolation.path}</span> – {it.isolation.note}</p>}
          <p className="text-[11px] text-muted">Executed: no · Opened: no · Static analysis only.</p>
        </div>
      )}
    </div>
  )
}

export default function SecurityGuardian() {
  const { c } = useOutletContext<CaseCtx>()
  const [g, setG] = useState<Guardian | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const load = () => adv.guardian(c.id).then(setG).catch(setErr)
  useEffect(() => { load() }, [c.id]) // eslint-disable-line react-hooks/exhaustive-deps
  const rescan = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await adv.rescan(c.id)
      const changed = r.results.filter(x => x.before !== x.after)
      setMsg(`${r.rescanned} artifact(s) re-scanned with ${r.engine ?? 'no antivirus engine (static rules only)'}; ${changed.length} status change(s). Nothing was executed.`)
      await load()
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  if (err) return <ErrorBox title="SECURITY SCAN FAILED" error={err} />
  if (!g) return <Spinner />
  return (
    <div className="space-y-4">
      <PageHeader icon={ShieldCheck} accent="#7a5bc7" title="Recovery Safety Guardian"
        subtitle="Every reconstructed artifact passes a static security stage before export or opening: antivirus signatures (ClamAV when available), YARA-style rules, embedded executables, scripts, macros, suspicious archives, dangerous types, polyglots and embedded objects. Nothing is ever executed."
        action={<button onClick={rescan} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">
          <RefreshCw className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />Run security scan</button>} />
      {msg && <Hint>{msg}</Hint>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Object.keys(GUARD_META).map(k => {
          const I = ICON[k as keyof typeof ICON]
          return (
            <div key={k} className="relative overflow-hidden rounded-2xl border border-line bg-card p-4 shadow-[var(--shadow-card)]">
              <div className="absolute inset-x-0 top-0 h-1" style={{ background: GUARD_META[k].color }} />
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-muted">{k}<I className="h-4 w-4" style={{ color: GUARD_META[k].color }} /></div>
              <div className="mt-1 font-mono text-[26px] font-bold text-ink">{g.counts[k] ?? 0}</div>
            </div>
          )
        })}
      </div>
      <Card title="Scanner configuration" subtitle={`Antivirus: ${g.engine ?? 'none available (ClamAV not installed/reachable)'} · Rules: ${g.rule_engine}${g.simulated_engine ? ' · DEMO: simulated engine' : ''}`}>
        <ul className="grid gap-1.5 text-xs text-ink2 md:grid-cols-2">{g.policy.map(p => <li key={p} className="flex gap-2"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />{p}</li>)}</ul>
        <p className="mt-2 text-[11px] text-muted">Unassigned fragments swept: {g.unassigned_scanned ?? 0} · flagged {g.unassigned_flagged ?? 0}</p>
      </Card>
      <div className="space-y-2">{[...g.items].sort((a, b) => ['MALWARE DETECTED', 'SUSPICIOUS', 'SCAN UNAVAILABLE', 'NO DETECTION'].indexOf(a.status) - ['MALWARE DETECTED', 'SUSPICIOUS', 'SCAN UNAVAILABLE', 'NO DETECTION'].indexOf(b.status)).map(it => <Item key={it.file_id} it={it} />)}</div>
    </div>
  )
}
