import { useEffect, useState } from 'react'
import { Scale } from 'lucide-react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link } from 'react-router-dom'
import { adv, type CalPart, type Calibration as Cal } from '../api-advanced'
import { Card, Empty, ErrorBox, Hint, PageHeader, SimBanner, Spinner } from '../components/ui'

function Part({ p, title }: { p: CalPart; title: string }) {
  if (!p.available) {
    return (
      <Card title={title}>
        <Empty title={p.status ?? 'CONFIDENCE CALIBRATION UNAVAILABLE — INSUFFICIENT VALIDATION DATA'}>
          {p.reason} <Link to="/benchmarks" className="font-semibold text-brand hover:underline">Open the Benchmark Lab →</Link>
        </Empty>
      </Card>
    )
  }
  const data = (p.bins ?? []).map(b => ({ ...b, ideal: b.lo + 5 }))
  const cm = p.confusion!
  return (
    <Card title={p.title ?? title} subtitle={`${p.n} labelled prediction(s), ${p.positives} confirmed · prediction = ${p.prediction}`}>
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div>
          <div className="h-64">
            <ResponsiveContainer>
              <ComposedChart data={data} margin={{ left: -16, right: 8, top: 8 }}>
                <CartesianGrid stroke="#eee6d6" vertical={false} />
                <XAxis dataKey="range" tick={{ fontSize: 10, fill: '#6b7c83' }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#6b7c83' }} unit="%" />
                <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }}
                  formatter={(v, n) => [v == null ? 'no samples' : `${v}%`, n === 'observed_rate' ? 'observed rate' : n === 'mean_predicted' ? 'mean predicted' : 'perfect calibration']} />
                <Bar dataKey="observed_rate" fill="#1fa6a6" radius={[3, 3, 0, 0]} />
                <Line dataKey="mean_predicted" stroke="#f4a340" strokeWidth={2} dot={{ r: 3 }} connectNulls />
                <Line dataKey="ideal" stroke="#8a9aa0" strokeDasharray="4 4" dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="text-center text-[11px] text-muted">Bars: observed validation rate per confidence band · orange: mean predicted confidence · grey dashed: perfect calibration</p>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2">
            {[['Brier score', p.brier, 'lower is better'], ['Expected calibration error', p.ece, 'lower is better'],
              ['Precision', p.precision, 'accepted & confirmed'], ['Recall', p.recall, 'confirmed & accepted']].map(([l, v, h]) => (
              <div key={l as string} className="rounded-xl border border-line bg-soft p-2.5">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">{l}</div>
                <div className="font-mono text-xl font-bold text-ink">{v == null ? 'N/A' : Number(v).toFixed(3)}</div>
                <div className="text-[10px] text-muted">{h}</div>
              </div>
            ))}
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">Confusion matrix</div>
            <table className="mt-1 w-full text-center text-xs">
              <thead><tr><th /><th className="py-1 text-muted">confirmed</th><th className="text-muted">not confirmed</th></tr></thead>
              <tbody>
                <tr><th className="text-right text-muted">predicted</th><td className="rounded bg-emerald-50 py-2 font-mono font-bold">{cm.tp}</td><td className="rounded bg-red-50 font-mono font-bold">{cm.fp}</td></tr>
                <tr><th className="text-right text-muted">not predicted</th><td className="rounded bg-amber-50 py-2 font-mono font-bold">{cm.fn}</td><td className="rounded bg-track font-mono font-bold">{cm.tn}</td></tr>
              </tbody>
            </table>
          </div>
          {p.classification_accuracy != null && <p className="text-xs text-ink2">File classification accuracy on matched artifacts: <b>{(p.classification_accuracy * 100).toFixed(1)}%</b></p>}
          {(p.bins ?? []).some(b => b.low_sample) && <p className="text-[11px] text-amber-900">Bands with fewer than 5 samples are statistically unreliable.</p>}
        </div>
      </div>
    </Card>
  )
}

export default function Calibration() {
  const [d, setD] = useState<Cal | null>(null)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => { adv.calibration().then(setD).catch(setErr) }, [])
  return (
    <div className="mx-auto max-w-[1500px] space-y-4 px-4 py-6">
      <PageHeader icon={Scale} accent="#0f4c5c" title="Confidence Calibration Engine"
        subtitle="Detection, relationship, reconstruction and classification confidence, integrity and recovery possibility are kept separate. Here, predicted confidence is compared with outcomes confirmed against ground truth. Statistics are never manufactured: if too few labelled predictions exist, calibration is reported as unavailable." />
      <SimBanner note="Labels exist only for synthetic images with known ground truth (demo datasets and Benchmark Lab runs); identical images are counted once.">SYNTHETIC TEST DATA</SimBanner>
      {err != null && <ErrorBox error={err} />}
      {!d && !err && <Spinner />}
      {d && (
        <>
          <Hint>{d.disclaimer}</Hint>
          <Part p={d.relationship} title="Relationship confidence vs. confirmed fragment links" />
          <Part p={d.integrity} title="Integrity score vs. byte-exact reconstruction" />
          <Card title="Validation data sources" subtitle={`${d.engine} · minimum ${d.min_samples} labelled predictions per measure`}>
            <ul className="grid gap-1 text-xs sm:grid-cols-2">{d.sources.map(s => (
              <li key={s.kind + s.id + s.dataset} className="flex justify-between rounded-lg bg-soft px-2.5 py-1.5">
                <span>{s.kind} · <span className="font-mono">{s.dataset}</span></span><span className="font-mono text-muted">{s.edges} link label(s)</span></li>))}</ul>
            {!d.sources.length && <p className="text-xs text-muted">No ground-truth data yet.</p>}
          </Card>
        </>
      )}
    </div>
  )
}
