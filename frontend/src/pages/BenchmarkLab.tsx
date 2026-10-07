import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Beaker, CheckCircle2, Circle, Loader2, Play } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link, useSearchParams } from 'react-router-dom'
import { adv, type BenchRow, type BenchRun } from '../api-advanced'
import { Card, CheckIcon, ErrorBox, Hint, PageHeader, SimBanner, Spinner, Tag } from '../components/ui'
import { STATUS_META } from '../lib/format'

const METRICS: [string, string, boolean][] = [
  ['byte_recovery_rate', 'Byte recovery rate', true], ['linking_precision', 'Fragment-linking precision', true],
  ['linking_recall', 'Fragment-linking recall', true], ['classification_accuracy', 'File classification accuracy', true],
  ['reconstruction_accuracy', 'Reconstruction accuracy', true], ['integrity_prediction_accuracy', 'Integrity prediction accuracy', true],
  ['false_positive_rate', 'False positive rate', false], ['false_negative_rate', 'False negative rate', false],
]
const fmt = (v: number | null | undefined) => (v == null ? 'N/A' : `${v}%`)

function Pipeline({ stages, stage, running }: { stages: string[]; stage: string; running: boolean }) {
  const cur = stages.findIndex(s => stage.endsWith(s))
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {stages.map((s, i) => {
        const done = !running || i < cur
        const active = running && i === cur
        return (
          <motion.div key={s} layout className="flex items-center gap-1.5">
            <motion.span animate={{ scale: active ? 1.08 : 1 }} transition={{ repeat: active ? Infinity : 0, repeatType: 'reverse', duration: 0.6 }}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-semibold ring-1 ring-inset ${active ? 'bg-accent text-brand-deep ring-accent' : done ? 'bg-emerald-50 text-emerald-800 ring-emerald-300' : 'bg-track text-muted ring-line2'}`}>
              {active ? <Loader2 className="h-3 w-3 animate-spin" /> : done ? <CheckCircle2 className="h-3 w-3" /> : <Circle className="h-3 w-3" />}{s}
            </motion.span>
            {i < stages.length - 1 && <span className="text-muted">↓</span>}
          </motion.div>
        )
      })}
    </div>
  )
}

export default function BenchmarkLab() {
  const [meta, setMeta] = useState<{ scenarios: Record<string, { title: string; damage: string }>; stages: string[]; label: string } | null>(null)
  const [runs, setRuns] = useState<BenchRow[] | null>(null)
  const [pick, setPick] = useState<Set<string>>(new Set())
  const [seed, setSeed] = useState('')
  const [sp, setSp] = useSearchParams()
  const [run, setRun] = useState<BenchRun | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const bid = sp.get('run') ?? runs?.[0]?.id
  useEffect(() => { adv.benchScenarios().then(m => { setMeta(m); setPick(new Set(Object.keys(m.scenarios))) }).catch(setErr); adv.benchList().then(setRuns) }, [])
  useEffect(() => {
    if (!bid) return
    let stop = false
    const tick = async () => {
      try {
        const r = await adv.bench(bid)
        if (stop) return
        setRun(r)
        if (r.status === 'running' || r.status === 'queued') setTimeout(tick, 900)
        else adv.benchList().then(setRuns)
      } catch (e) { setErr(e) }
    }
    tick()
    return () => { stop = true }
  }, [bid])
  const start = async () => {
    setErr(null)
    try {
      const { id } = await adv.benchStart([...pick], seed ? Number(seed) : undefined)
      const n = new URLSearchParams(sp); n.set('run', id); setSp(n)
      adv.benchList().then(setRuns)
    } catch (e) { setErr(e) }
  }
  const running = run?.status === 'running' || run?.status === 'queued'
  const chart = run?.data.scenarios.map(s => ({ name: s.title, 'Byte recovery': s.metrics.byte_recovery_rate, 'Link precision': s.metrics.linking_precision,
    'Link recall': s.metrics.linking_recall, 'Reconstruction acc.': s.metrics.reconstruction_accuracy })) ?? []
  return (
    <div className="mx-auto max-w-[1500px] space-y-4 px-4 py-6">
      <PageHeader icon={Beaker} accent="#eb6834" title="Recovery Benchmark Lab"
        subtitle="Controlled evaluation of the recovery engine: clean files are kept as GROUND TRUTH, one damage type is applied, the unmodified engine runs on the damaged image, and the result is compared with the truth. Every number is measured, never estimated." />
      <SimBanner note="Synthetic images with simulated damage. Results are not real-world forensic findings.">SYNTHETIC TEST DATA</SimBanner>
      {err != null && <ErrorBox title="Benchmark failed" error={err} />}
      <Card title="Configure run" subtitle="Choose damage scenarios; a seed makes a run reproducible (blank = random).">
        {!meta ? <Spinner /> : (
          <>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(meta.scenarios).map(([k, s]) => (
                <label key={k} className={`flex cursor-pointer gap-2 rounded-xl border p-2.5 text-xs transition ${pick.has(k) ? 'border-teal bg-tealsoft' : 'border-line bg-soft'}`}>
                  <input type="checkbox" checked={pick.has(k)} onChange={() => { const n = new Set(pick); if (n.has(k)) n.delete(k); else n.add(k); setPick(n) }} className="mt-0.5 accent-teal" />
                  <span><b className="text-ink">{s.title}</b><span className="block text-muted">{s.damage}</span></span>
                </label>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <label className="text-xs text-muted">Seed <input value={seed} onChange={e => setSeed(e.target.value.replace(/\D/g, ''))} placeholder="random"
                className="ml-1 w-28 rounded-lg border border-line2 bg-soft px-2 py-1 font-mono text-sm text-ink" /></label>
              <button onClick={start} disabled={!pick.size || running} className="inline-flex items-center gap-1.5 rounded-full bg-accent px-5 py-2 text-sm font-bold text-brand-deep shadow hover:bg-accent-2 disabled:opacity-50">
                <Play className="h-4 w-4" />RUN BENCHMARK</button>
              <span className="text-[11px] text-muted">≈2 s per scenario. Completed runs also feed the confidence calibration (<Link to="/calibration" className="text-brand hover:underline">view</Link>).</span>
            </div>
          </>
        )}
      </Card>
      {run && meta && (
        <Card title={<span className="flex items-center gap-2">Run {run.id} <Tag tone={run.status === 'complete' ? 'text-emerald-800 bg-emerald-50 ring-emerald-300' : run.status === 'failed' ? 'text-red-800 bg-red-50 ring-red-300' : 'text-amber-900 bg-amber-50 ring-amber-300'}>{run.status.toUpperCase()}</Tag></span>}
          subtitle={`seed ${run.seed} · ${run.data.planned?.length ?? 0} scenario(s) · started ${run.created.replace('T', ' ').slice(0, 19)}`}>
          <Pipeline stages={meta.stages} stage={run.stage} running={running} />
          {running && <div className="mt-3 h-2 overflow-hidden rounded-full bg-track"><motion.div className="h-full bg-accent" animate={{ width: `${run.progress * 100}%` }} /></div>}
          <div className="mt-2 text-xs text-muted">{running ? run.stage : run.error ? `Error: ${run.error.split('\n').slice(-2).join(' ')}` : 'Complete'}</div>
          <AnimatePresence>
            <ol className="mt-3 max-h-40 space-y-0.5 overflow-y-auto rounded-lg bg-soft p-2 font-mono text-[11px] text-ink2 scroll-thin">
              {run.data.log.slice(-40).map((l, i) => <motion.li key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>{l.ts.slice(11, 19)} {l.message}</motion.li>)}
            </ol>
          </AnimatePresence>
        </Card>
      )}
      {run?.data.aggregate && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {METRICS.map(([k, l, good]) => {
            const a = run.data.aggregate![k]
            return (
              <div key={k} className="relative overflow-hidden rounded-2xl border border-line bg-card p-4 shadow-[var(--shadow-card)]">
                <div className="absolute inset-x-0 top-0 h-1" style={{ background: good ? '#1fa6a6' : '#d9534f' }} />
                <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">{l}</div>
                <div className="font-mono text-[26px] font-bold text-ink">{fmt(a?.mean)}</div>
                <div className="text-[11px] text-muted">mean over {a?.n ?? 0} scenario(s) · {good ? 'higher is better' : 'lower is better'}</div>
              </div>
            )
          })}
        </div>
      )}
      {run && run.data.scenarios.length > 0 && (
        <>
          <Card title="Metrics by scenario" subtitle="SYNTHETIC TEST DATA">
            <div className="h-72">
              <ResponsiveContainer>
                <BarChart data={chart} margin={{ left: -16, right: 8, top: 8 }}>
                  <CartesianGrid stroke="#eee6d6" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#6b7c83' }} interval={0} angle={-15} textAnchor="end" height={50} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#6b7c83' }} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="Byte recovery" fill="#1fa6a6" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="Link precision" fill="#0f4c5c" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="Link recall" fill="#f4a340" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="Reconstruction acc." fill="#7a5bc7" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card title="Ground truth vs. recovered result" bodyClass="p-0">
            <div className="overflow-x-auto"><table className="w-full min-w-[1100px] text-xs">
              <thead className="bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr>
                <th className="px-4 py-2">Scenario</th><th>Ground truth (surviving)</th><th>Recovered</th>{METRICS.map(([k, l]) => <th key={k} className="pr-2">{l.replace('File ', '').replace('Fragment-linking', 'Link')}</th>)}</tr></thead>
              <tbody>{run.data.scenarios.map(s => (
                <tr key={s.scenario} className="border-t border-line align-top">
                  <td className="px-4 py-2"><b className="text-ink">{s.title}</b><div className="text-[10px] text-muted">{s.damage}</div></td>
                  <td className="pr-2">{s.ground_truth.map(g => <div key={g.name}>{g.name} <span className="text-muted">({g.surviving_pct}%)</span></div>)}</td>
                  <td className="pr-2">{s.recovered.map(r => <div key={r.name} className="flex items-center gap-1">
                    <CheckIcon status={s.file_records.find(x => x.file === r.matched_truth)?.exact ? 'pass' : r.matched_truth ? 'partial' : 'na'} />
                    <span className="truncate" title={r.name}>{r.name}</span>
                    <span className="text-[10px]" style={{ color: STATUS_META[r.status as keyof typeof STATUS_META]?.color }}>{r.status.replaceAll('_', ' ').toLowerCase()}</span></div>)}</td>
                  {METRICS.map(([k]) => <td key={k} className="pr-2 font-mono">{fmt(s.metrics[k as keyof typeof s.metrics] as number | null)}</td>)}
                </tr>))}</tbody></table></div>
            <p className="border-t border-line px-4 py-2 text-[11px] text-muted">✔ = recovered bytes are byte-identical to the ground-truth file (SHA-256). Linking is measured at fragment junctions; N/A = no junction existed to evaluate.</p>
          </Card>
        </>
      )}
      <Card title="Previous runs" bodyClass="p-0">
        {!runs ? <Spinner /> : !runs.length ? <p className="p-4 text-xs text-muted">No benchmark has been run yet.</p> : (
          <table className="w-full text-xs"><tbody>{runs.map(r => (
            <tr key={r.id} onClick={() => { const n = new URLSearchParams(sp); n.set('run', r.id); setSp(n) }} className={`cursor-pointer border-t border-line hover:bg-tealsoft ${r.id === bid ? 'bg-tealsoft' : ''}`}>
              <td className="px-4 py-2 font-mono">{r.id}</td><td>{r.created.replace('T', ' ').slice(0, 19)}</td><td>{r.status}</td><td>{r.scenarios} scenario(s)</td>
              <td>seed {r.seed}</td><td className="pr-4 font-mono">byte recovery {fmt(r.aggregate?.byte_recovery_rate?.mean)}</td></tr>))}</tbody></table>
        )}
      </Card>
      <Hint>{run?.data.note ?? 'Benchmark results measure the engine on synthetic data only and must never be presented as real-world forensic findings.'}</Hint>
    </div>
  )
}
