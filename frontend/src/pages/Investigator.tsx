import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Ban, Bot, Check, Eye, RefreshCw, Sparkles, X } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { api, type FileRow } from '../api'
import { adv, type InvState, type Rec } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, DataClass, Empty, ErrorBox, Hint, PageHeader, SimBanner, Spinner, Tag } from '../components/ui'

const CONF_TONE = { HIGH: 'text-emerald-800 bg-emerald-50 ring-emerald-300', MEDIUM: 'text-amber-900 bg-amber-50 ring-amber-300', LOW: 'text-ink2 bg-track ring-line2' }
const STATUS_TONE: Record<string, string> = { pending: 'text-sky-900 bg-sky-50 ring-sky-300', executed: 'text-emerald-800 bg-emerald-50 ring-emerald-300',
  rejected: 'text-ink2 bg-track ring-line2', failed: 'text-red-800 bg-red-50 ring-red-300' }

function Field({ k, children }: { k: string; children: React.ReactNode }) {
  return <div><div className="text-[10px] font-bold uppercase tracking-wider text-muted">{k}</div><div className="text-xs text-ink">{children}</div></div>
}

function RecCard({ r, n, onDecide, busy }: { r: Rec; n: number; onDecide: (id: string, d: 'approve' | 'reject') => void; busy: boolean }) {
  const [ev, setEv] = useState(false)
  const [detail, setDetail] = useState(false)
  const lines = (r.result?.detail as { lines?: { offset: string; hex: string; ascii: string }[] } | undefined)?.lines
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className={`rounded-2xl border bg-card p-4 shadow-[var(--shadow-card)] ${r.status === 'pending' ? 'border-sky-200' : 'border-line opacity-95'}`}>
      <div className="flex flex-wrap items-start gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-brand font-mono text-xs font-bold text-white">{n}</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-ink">{r.title}</div>
          <div className="text-[11px] text-muted"><span className="font-mono">{r.id}</span> · {r.action_label}</div>
        </div>
        <Tag tone={CONF_TONE[r.confidence]}>CONFIDENCE {r.confidence}</Tag>
        <Tag tone={STATUS_TONE[r.status]}>{r.status.toUpperCase()}</Tag>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <Field k="Action">{r.action_label}</Field>
        <Field k="Reason">{r.reason}</Field>
        <Field k="Expected benefit">{r.expected_benefit}</Field>
        <Field k="Risk">{r.risk}</Field>
      </div>
      <p className="mt-2 text-[11px] text-muted">Confidence basis: {r.confidence_basis}</p>
      <AnimatePresence>{ev && (
        <motion.ul initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
          className="mt-2 list-disc space-y-0.5 overflow-hidden rounded-lg bg-soft py-2 pl-7 pr-3 text-xs text-ink2">
          {r.evidence.map(e => <li key={e}>{e}</li>)}
        </motion.ul>)}</AnimatePresence>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {r.status === 'pending' && <>
          <button disabled={busy} onClick={() => onDecide(r.id, 'approve')} className="inline-flex items-center gap-1 rounded-full bg-success px-4 py-1.5 text-xs font-bold text-white hover:brightness-95 disabled:opacity-50"><Check className="h-3.5 w-3.5" />APPROVE</button>
          <button disabled={busy} onClick={() => onDecide(r.id, 'reject')} className="inline-flex items-center gap-1 rounded-full bg-card px-4 py-1.5 text-xs font-bold text-danger ring-1 ring-red-300 hover:bg-red-50 disabled:opacity-50"><X className="h-3.5 w-3.5" />REJECT</button>
        </>}
        <button onClick={() => setEv(!ev)} className="inline-flex items-center gap-1 rounded-full bg-tealsoft px-4 py-1.5 text-xs font-bold text-brand hover:brightness-95"><Eye className="h-3.5 w-3.5" />{ev ? 'HIDE EVIDENCE' : 'VIEW EVIDENCE'}</button>
        {r.decision_ts && <span className="text-[11px] text-muted">{r.decision} {r.decision_ts.replace('T', ' ').slice(0, 19)}</span>}
      </div>
      {r.result && (
        <div className={`mt-3 rounded-xl border p-3 text-xs ${r.status === 'failed' ? 'border-red-200 bg-red-50 text-red-900' : 'border-emerald-200 bg-emerald-50/60 text-ink'}`}>
          <div className="mb-1 flex flex-wrap items-center gap-2"><b>Result</b>
            {r.result.label.includes('SIMULATION') ? <DataClass kind="simulated" /> : r.result.label === 'OBSERVED' ? <DataClass kind="observed" /> : r.result.label.includes('INFERRED') ? <DataClass kind="inferred" /> : <DataClass kind="derived" />}
            <button onClick={() => setDetail(!detail)} className="ml-auto text-[11px] font-semibold text-brand hover:underline">{detail ? 'hide details' : 'details'}</button></div>
          {r.result.label.includes('SIMULATION') && <div className="mb-1"><SimBanner /></div>}
          <div>{r.result.summary}</div>
          {detail && (lines ? (
            <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-white p-2 font-mono text-[11px] scroll-thin">{lines.map(l => `${l.offset}  ${l.hex.padEnd(47)}  ${l.ascii}`).join('\n')}</pre>
          ) : <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-white p-2 font-mono text-[10px] scroll-thin">{JSON.stringify(r.result.detail, null, 1).slice(0, 6000)}</pre>)}
        </div>
      )}
    </motion.div>
  )
}

export default function Investigator() {
  const { c } = useOutletContext<CaseCtx>()
  const [st, setSt] = useState<InvState | null>(null)
  const [files, setFiles] = useState<FileRow[]>([])
  const [focus, setFocus] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const [nar, setNar] = useState<{ text: string | null; status: string; note: string; label?: string; error?: string } | null>(null)
  useEffect(() => { adv.investigator(c.id).then(setSt).catch(setErr); api.files(c.id).then(setFiles) }, [c.id])
  const plan = async () => { setBusy(true); setErr(null); try { setSt(await adv.plan(c.id, focus)) } catch (e) { setErr(e) } finally { setBusy(false) } }
  const decide = async (id: string, d: 'approve' | 'reject') => {
    setBusy(true); setErr(null)
    try { await adv.decide(c.id, id, d); setSt(await adv.investigator(c.id)) } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const narrative = async () => { setBusy(true); try { setNar(await adv.narrative(c.id)) } catch (e) { setErr(e) } finally { setBusy(false) } }
  const pending = st?.recommendations.filter(r => r.status === 'pending') ?? []
  const done = st?.recommendations.filter(r => r.status !== 'pending') ?? []
  return (
    <div className="space-y-4">
      <PageHeader icon={Bot} accent="#0f4c5c" title="AI Recovery Investigator"
        subtitle="An approval-gated investigation planner that reasons only over structured evidence from the backend (possibility map, Fragment DNA, structure, validation, security, cross-artifact links, your priority keywords). Nothing runs until you approve it, and every executable action is read-only or a simulation."
        action={<div className="flex flex-wrap items-center gap-2">
          <select value={focus} onChange={e => setFocus(e.target.value)} className="rounded-lg border border-line2 bg-card px-2 py-1.5 text-xs">
            <option value="">Whole case</option>{files.map(f => <option key={f.file_id} value={f.file_id}>{f.file_id} · {f.file_name}</option>)}</select>
          <button onClick={plan} disabled={busy} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">
            <RefreshCw className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />Generate suggested next steps</button></div>} />
      {err != null && <ErrorBox error={err} />}
      {!st && !err && <Spinner />}
      {st && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            <h2 className="text-sm font-bold uppercase tracking-wider text-ink2">Suggested next steps ({pending.length} awaiting approval)</h2>
            {!st.recommendations.length && <Empty title="No plan yet">Click "Generate suggested next steps". If the evidence supports no further action, the plan will be empty.</Empty>}
            {pending.map((r, i) => <RecCard key={r.id} r={r} n={i + 1} onDecide={decide} busy={busy} />)}
            {done.length > 0 && <h2 className="pt-2 text-sm font-bold uppercase tracking-wider text-ink2">Decided</h2>}
            {done.map((r, i) => <RecCard key={r.id} r={r} n={pending.length + i + 1} onDecide={decide} busy={busy} />)}
          </div>
          <div className="space-y-4">
            <Card icon={Sparkles} accent="#7a5bc7" title="Planner" subtitle={st.planner}>
              <div className="text-xs">
                <Tag tone={st.llm.configured ? 'text-emerald-800 bg-emerald-50 ring-emerald-300' : 'text-ink2 bg-track ring-line2'}>{st.llm.status}</Tag>
                <p className="mt-2 text-ink2">{st.llm.note}</p>
                <button onClick={narrative} disabled={busy || !st.llm.configured} className="mt-2 rounded-full bg-tealsoft px-3 py-1.5 text-xs font-semibold text-brand disabled:opacity-50">Generate narrative summary</button>
                {nar && (nar.text ? <div className="mt-2 rounded-lg bg-soft p-2"><div className="mb-1 text-[10px] font-bold uppercase text-purple">{nar.label}</div><p className="whitespace-pre-wrap">{nar.text}</p></div>
                  : <p className="mt-2 text-muted">{nar.error ?? nar.note}</p>)}
              </div>
            </Card>
            <Card icon={Ban} accent="#d9534f" title="The investigator never">
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink2">{st.never.map(n => <li key={n}>{n}</li>)}</ul>
            </Card>
            <Hint>LLM assistance is limited to summarising and explaining structured evidence. Binary parsing, validation, hashing, malware scanning and evidence preservation are always deterministic.</Hint>
          </div>
          <Card title="Investigator audit log" subtitle="Timestamp · recommendation · evidence · user decision · action taken · result" className="xl:col-span-2" bodyClass="p-0">
            <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-xs">
              <thead className="bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr><th className="px-4 py-2">Timestamp</th><th>Recommendation</th><th>Evidence</th><th>User decision</th><th>Action taken</th><th>Result</th></tr></thead>
              <tbody>{st.log.map((l, i) => (
                <tr key={i} className="border-t border-line align-top">
                  <td className="px-4 py-1.5 font-mono text-[11px]">{l.timestamp.replace('T', ' ').slice(0, 19)}</td><td className="pr-2">{l.recommendation}</td>
                  <td className="pr-2 text-ink2">{l.evidence[0]}</td><td className="pr-2 font-semibold uppercase">{l.decision}</td><td className="pr-2">{l.action_taken}</td>
                  <td className="pr-4 text-ink2">{l.result ?? '—'}</td></tr>))}</tbody></table></div>
          </Card>
        </div>
      )}
    </div>
  )
}
