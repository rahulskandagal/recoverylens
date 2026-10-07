import { useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api, type ModelCard } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, Hint, Stat } from '../components/ui'
import { pct } from '../lib/format'

export default function Evaluation() {
  const { c } = useOutletContext<CaseCtx>()
  const ev = c.evaluation
  const [model, setModel] = useState<ModelCard | null>(c.model)
  useEffect(() => { if (!model) api.model().then(setModel) }, [model])
  if (!ev) return <p className="text-sm text-muted">Ground-truth evaluation is only available for demo datasets.</p>
  const tr = model?.training ?? {}
  const maxW = Math.max(...(model?.features.map(f => Math.abs(f.weight)) ?? [1]))
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Ground-truth check</h1>
        <p className="text-sm text-muted">The demo generator recorded where every cluster really belonged. The engine never reads that record; it is compared only <i>after</i> analysis, to show where the engine is right and where it is not.</p>
      </div>
      <Hint>{ev.disclaimer}</Hint>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Placement precision" value={pct(ev.cluster_placement_precision, 1)} hint="placed clusters at their true position" accent="#22a06b" />
        <Stat label="Cluster recall" value={pct(ev.cluster_recall, 1)} hint="surviving clusters correctly recovered" />
        <Stat label="Link accuracy" value={pct(ev.edge_accuracy, 1)} hint={`${ev.edges_evaluated} accepted links`} />
        <Stat label="Wrong links" value={ev.wrong_edges.length} hint={ev.wrong_edges.slice(0, 2).join(', ') || 'none'} accent={ev.wrong_edges.length ? '#f4a340' : '#22a06b'} />
        <Stat label="Bit-rot detected" value={`${ev.corruption_bytes_detected}/${ev.corruption_bytes_injected}`} hint="injected flipped bytes flagged" accent="#f4a340" />
      </div>
      <Card title="Per-file comparison" subtitle="True surviving share vs. what the engine reported">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[820px] text-xs">
            <thead className="text-left text-[10px] uppercase tracking-wider text-muted"><tr><th className="py-1.5">Original file (truth)</th><th>Truly surviving</th><th>Engine reconstruction</th><th>Correct clusters</th><th>Engine status</th><th>Damage injected</th></tr></thead>
            <tbody>
              {ev.files.map(f => (
                <tr key={f.name} className="border-t border-line text-ink2">
                  <td className="py-1.5">{f.engine_candidate ? <Link to={`../files/${f.engine_candidate}`} className="text-brand hover:underline">{f.name}</Link> : f.name}</td>
                  <td className="font-mono">{pct(f.true_surviving_pct, 1)} <span className="text-muted/70">({f.surviving_clusters}/{f.clusters})</span></td>
                  <td className="font-mono">{pct(f.engine_reconstruction_pct, 1)}</td>
                  <td className="font-mono">{f.correctly_recovered_clusters}/{f.surviving_clusters}</td>
                  <td>{f.engine_status.replace(/_/g, ' ').toLowerCase()}</td>
                  <td className="text-muted">{[f.header_lost && 'header overwritten', f.bytes_flipped && `${f.bytes_flipped} flipped byte(s)`, f.surviving_clusters < f.clusters && `${f.clusters - f.surviving_clusters} cluster(s) overwritten`].filter(Boolean).join(', ') || '—'}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[11px] text-muted">{ev.corruption_note} Where structure allows (SQLite pages, ZIP CRC, PDF stream lengths, JPEG marker legality), damage is detected; elsewhere it is reported as a limitation.</p>
      </Card>
      {model && (
        <Card title="Edge model card" subtitle={model.type}>
          <div className="grid gap-5 lg:grid-cols-2">
            <div>
              <div className="mb-2 text-[11px] uppercase tracking-wider text-muted">Learned weights (standardised features)</div>
              {model.features.map(f => (
                <div key={f.name} className="grid grid-cols-[190px_1fr_48px] items-center gap-2 py-0.5 text-xs">
                  <span className="text-ink2">{f.label}</span>
                  <div className="relative h-2 rounded bg-track">
                    <div className="absolute h-2 rounded" style={{ left: f.weight >= 0 ? '50%' : `${50 - 50 * Math.abs(f.weight) / maxW}%`, width: `${50 * Math.abs(f.weight) / maxW}%`, background: f.weight >= 0 ? '#3987e5' : '#e66767' }} />
                    <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-slate-500" />
                  </div>
                  <span className="text-right font-mono text-muted">{f.weight.toFixed(2)}</span>
                </div>))}
            </div>
            <div className="space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-2 font-mono">
                {[['Held-out AUC', tr.test_auc], ['Held-out accuracy', tr.test_accuracy], ['Precision', tr.test_precision], ['Recall', tr.test_recall],
                  ['Train samples', tr.train_samples], ['Test samples', tr.test_samples], ['Prior-weights AUC', tr.prior_test_auc], ['Positive rate', tr.test_positive_rate]].map(([k, v]) => (
                  <div key={String(k)} className="rounded bg-soft px-2 py-1.5"><div className="font-sans text-[10px] uppercase text-muted">{k}</div><div className="text-ink">{String(v ?? '—')}</div></div>))}
              </div>
              <p className="text-muted">{String(tr.data ?? '')}</p>
              <ul className="list-disc space-y-1 pl-4 text-muted">{model.limitations.map(l => <li key={l}>{l}</li>)}</ul>
            </div>
          </div>
        </Card>
      )}
    </div>
  )
}
