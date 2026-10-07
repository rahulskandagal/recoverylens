import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowRight, FlaskConical, Play, ShieldCheck, TriangleAlert } from 'lucide-react'
import { adv, type Region, type SimEval, type SimResult } from '../api-advanced'
import { CheckIcon, ErrorBox, SimBanner, Spinner, Tag } from './ui'

function Side({ title, e, tone }: { title: string; e: SimEval; tone: string }) {
  return (
    <div className={`rounded-xl border p-3 ${tone}`}>
      <div className="text-[11px] font-bold uppercase tracking-wider">{title}</div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <div><div className="text-[10px] uppercase text-muted">Integrity</div><div className="font-mono text-2xl font-bold text-ink">{e.integrity}</div></div>
        <div><div className="text-[10px] uppercase text-muted">Reconstruction</div><div className="font-mono text-2xl font-bold text-ink">{e.reconstruction == null ? 'N/A' : `${e.reconstruction}%`}</div></div>
      </div>
      <div className="mt-2 text-[11px] text-ink2">Validation: <b>{e.validation_state.toUpperCase()}</b> · {e.missing_ranges} missing range(s), {e.missing_bytes.toLocaleString()} B</div>
      <div className="text-[11px] text-muted">Status rule: {e.status.replaceAll('_', ' ').toLowerCase()}</div>
    </div>
  )
}

/** Counterfactual simulation of one candidate fragment in one missing range (temporary copies only). */
export default function SimulatorPanel({ caseId, fileId, region, defaultFragment }: {
  caseId: string; fileId: string; region: Region; defaultFragment?: string
}) {
  const [frag, setFrag] = useState(defaultFragment ?? region.candidates[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<SimResult | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const run = async () => {
    setBusy(true); setErr(null); setRes(null)
    try { setRes(await adv.simulate(caseId, fileId, frag.trim(), region.index)) } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  const rejected = res?.verdict.includes('REJECTED')
  const improved = res?.verdict.includes('IMPROVES')
  return (
    <div className="space-y-3 rounded-2xl border border-fuchsia-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <FlaskConical className="h-4 w-4 text-fuchsia-700" />
        <span className="text-sm font-semibold text-ink">Counterfactual Recovery Simulator</span>
        <span className="text-xs text-muted">missing range #{region.index} · {region.offset_hex} · {region.length.toLocaleString()} B</span>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-muted">Candidate fragment
          <input list={`cands-${region.index}`} value={frag} onChange={e => setFrag(e.target.value)} placeholder="F0123"
            className="mt-0.5 block w-40 rounded-lg border border-line2 bg-soft px-2.5 py-1.5 font-mono text-sm text-ink outline-none focus:border-teal" />
          <datalist id={`cands-${region.index}`}>{region.candidates.map(c => <option key={c.id} value={c.id}>{c.strength}</option>)}</datalist>
        </label>
        <button onClick={run} disabled={busy || !frag.trim()}
          className="inline-flex items-center gap-1.5 rounded-full bg-fuchsia-700 px-4 py-2 text-sm font-semibold text-white hover:bg-fuchsia-800 disabled:opacity-50">
          <Play className="h-4 w-4" />Simulate
        </button>
        <span className="text-[11px] text-muted">Runs on temporary copies; the evidence and the export are never modified and nothing is saved.</span>
      </div>
      {busy && <Spinner label="Re-running validators on temporary copies…" />}
      {err != null && <ErrorBox title="SIMULATION FAILED" error={err} />}
      <AnimatePresence>
        {res && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="space-y-3">
            <SimBanner note={`${res.method}. Simulated copy SHA-256 ${res.simulated_sha256.slice(0, 16)}… (discarded).`} />
            <div className={`flex items-start gap-2 rounded-xl px-3 py-2 text-sm font-semibold ${rejected ? 'bg-red-50 text-red-900' : improved ? 'bg-emerald-50 text-emerald-900' : 'bg-track text-ink2'}`}>
              {rejected ? <TriangleAlert className="mt-0.5 h-4 w-4" /> : <ShieldCheck className="mt-0.5 h-4 w-4" />}
              <div>{res.verdict}<div className="text-xs font-normal">{res.verdict_reason}</div></div>
            </div>
            <div className="grid items-center gap-2 md:grid-cols-[1fr_auto_1fr]">
              <Side title="Before (as reconstructed)" e={res.before} tone="border-line bg-soft" />
              <ArrowRight className="mx-auto hidden h-5 w-5 text-fuchsia-700 md:block" />
              <Side title="Simulated result" e={res.after} tone="border-fuchsia-300 bg-fuchsia-50/60" />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">Validation changes</div>
                {res.changed_checks.length ? (
                  <ul className="mt-1 space-y-1 text-xs">
                    {res.changed_checks.map(c => (
                      <li key={c.check} className="flex items-center gap-1.5"><CheckIcon status={c.before ?? 'na'} /><ArrowRight className="h-3 w-3 text-muted" /><CheckIcon status={c.after ?? 'na'} />
                        <span className={c.direction === 'improved' ? 'text-emerald-800' : 'text-red-800'}>{c.check}</span></li>
                    ))}
                  </ul>
                ) : <p className="mt-1 text-xs text-muted">No validator result changed.</p>}
                {res.newly_resolved_ranges.map(r => <p key={r.start} className="mt-1 text-xs text-ink2">Range {r.start.toLocaleString()}–{r.end.toLocaleString()} filled <b>in the simulation only</b>.</p>)}
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">New conflicts</div>
                {res.new_conflicts.length ? <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-red-900">{res.new_conflicts.map(c => <li key={c}>{c}</li>)}</ul>
                  : <p className="mt-1 text-xs text-muted">None detected.</p>}
                <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                  <Tag tone="text-ink2 bg-track ring-line2" title="Byte-coverage factors rise whenever a gap is filled with anything; only the validator part is evidence that the bytes fit.">
                    Δ integrity {res.delta.integrity > 0 ? '+' : ''}{res.delta.integrity} (validators {res.delta.integrity_from_validators > 0 ? '+' : ''}{res.delta.integrity_from_validators}, byte coverage {res.delta.integrity_from_coverage > 0 ? '+' : ''}{res.delta.integrity_from_coverage})</Tag>
                  <Tag tone="text-ink2 bg-track ring-line2">Δ reconstruction {res.delta.reconstruction == null ? 'N/A' : `${res.delta.reconstruction > 0 ? '+' : ''}${res.delta.reconstruction}%`}</Tag>
                  <Tag tone="text-fuchsia-800 bg-fuchsia-50 ring-fuchsia-300">evidence modified: no · persisted: no</Tag>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
