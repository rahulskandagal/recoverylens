import { useEffect, useState } from 'react'
import { Link, useOutletContext } from 'react-router-dom'
import { api, type Criteria, type FileRow } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, ErrorBox, Hint, PriorityBadge, Spinner } from '../components/ui'

export default function Priorities() {
  const { c, reload } = useOutletContext<CaseCtx>()
  const [crit, setCrit] = useState<Criteria>(c.criteria)
  const [kw, setKw] = useState(c.criteria.keywords.join(', '))
  const [files, setFiles] = useState<FileRow[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => { api.files(c.id).then(setFiles) }, [c.id])
  const apply = async () => {
    setBusy(true)
    try {
      const next = { ...crit, keywords: kw.split(',').map(s => s.trim()).filter(Boolean) }
      const r = await api.putCriteria(c.id, next)
      setCrit(r.criteria)
      setFiles(r.files)
      reload()
    } catch (e) { setErr(e) }
    setBusy(false)
  }
  const wsum = Object.values(crit.weights).reduce((a, b) => a + b, 0) || 1
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Priority criteria</h1>
        <p className="text-sm text-muted">Priority is a transparent weighted sum of evidence scores and your investigation criteria. Change the criteria and every item is re-ranked, with an explanation.</p>
      </div>
      {err != null && <ErrorBox error={err} />}
      <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <Card title="Criteria" className="self-start">
          <div className="space-y-4 text-sm">
            <div>
              <label className="text-xs text-muted">Keywords (match in names, recovered text and metadata)</label>
              <input value={kw} onChange={e => setKw(e.target.value)} className="mt-1 w-full rounded-lg border border-line2 bg-soft px-3 py-2 font-mono text-sm outline-none focus:border-teal" />
            </div>
            <div>
              <div className="mb-1 text-xs text-muted">Score weights (normalised: {Object.entries(crit.weights).map(([k, v]) => `${k} ${Math.round(100 * v / wsum)}%`).join(' · ')})</div>
              {(Object.keys(crit.weights) as (keyof Criteria['weights'])[]).map(k => (
                <label key={k} className="grid grid-cols-[120px_1fr_40px] items-center gap-2 py-1 text-xs capitalize text-ink2">{k}
                  <input type="range" min={0} max={1} step={0.05} value={crit.weights[k]} onChange={e => setCrit({ ...crit, weights: { ...crit.weights, [k]: +e.target.value } })} className="accent-teal" />
                  <span className="text-right font-mono">{crit.weights[k].toFixed(2)}</span></label>))}
            </div>
            <div>
              <div className="mb-1 text-xs text-muted">Content-type preference</div>
              {Object.keys(crit.type_weights).map(k => (
                <label key={k} className="grid grid-cols-[120px_1fr_40px] items-center gap-2 py-1 text-xs text-ink2">{k}
                  <input type="range" min={0} max={1} step={0.1} value={crit.type_weights[k]} onChange={e => setCrit({ ...crit, type_weights: { ...crit.type_weights, [k]: +e.target.value } })} className="accent-teal" />
                  <span className="text-right font-mono">{crit.type_weights[k].toFixed(1)}</span></label>))}
            </div>
            <label className="grid grid-cols-[120px_1fr_40px] items-center gap-2 text-xs text-ink2">Recency window
              <input type="range" min={1} max={365} value={crit.recency_days} onChange={e => setCrit({ ...crit, recency_days: +e.target.value })} className="accent-teal" />
              <span className="text-right font-mono">{crit.recency_days}d</span></label>
            <button onClick={apply} disabled={busy} className="w-full rounded-lg bg-brand px-4 py-2 font-medium text-white hover:bg-brand-deep disabled:opacity-50">{busy ? 'Recomputing…' : 'Apply & re-rank'}</button>
            <Hint>CRITICAL means "review this earlier under these criteria and the available evidence" — never "proven important". Every change is written to the audit trail.</Hint>
          </div>
        </Card>
        <Card title="Resulting order">
          {!files ? <Spinner /> : (
            <ol className="divide-y divide-line">
              {files.map((f, i) => (
                <li key={f.file_id} className="grid grid-cols-[28px_110px_1fr_60px] items-center gap-2 py-2 text-sm">
                  <span className="font-mono text-xs text-muted/70">{i + 1}</span><PriorityBadge level={f.priority_level} />
                  <div className="min-w-0"><Link to={`../files/${f.file_id}`} className="block truncate text-ink hover:text-teal">{f.file_name}</Link>
                    <div className="truncate text-[11px] text-muted">{f.priority_reason}</div></div>
                  <span className="text-right font-mono text-xs text-ink2">{f.priority_score.toFixed(1)}</span>
                </li>))}
            </ol>)}
        </Card>
      </div>
    </div>
  )
}
