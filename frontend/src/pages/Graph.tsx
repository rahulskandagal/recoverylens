import { useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { api, type EdgeT, type Graph } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import RelGraph, { EDGE_STYLE, EdgeLegend } from '../components/RelGraph'
import { Card, DataClass, Spinner, StatusBadge } from '../components/ui'
import { bytes, relLabel } from '../lib/format'

export default function GraphPage() {
  const { c } = useOutletContext<CaseCtx>()
  const [params, setParams] = useSearchParams()
  const [g, setG] = useState<Graph | null>(null)
  const [minConf, setMinConf] = useState(0)
  const [alts, setAlts] = useState(true)
  const [sel, setSel] = useState<string | null>(null)
  const [types, setTypes] = useState<Set<string>>(new Set(Object.keys(EDGE_STYLE)))
  const focus = params.get('focus')
  useEffect(() => { api.graph(c.id).then(setG) }, [c.id])
  const selNode = useMemo(() => g?.nodes.find(n => n.id === sel), [g, sel])
  const selEdge = useMemo(() => {
    if (!g || !sel?.startsWith('edge:')) return null
    const [a, b] = sel.slice(5).split('>')
    return g.edges.find(e => e.src === a && e.dst === b) ?? null
  }, [g, sel])
  if (!g) return <Spinner />
  const files = g.nodes.filter(n => n.kind === 'file')
  const related = sel ? g.edges.filter(e => (e.src === sel || e.dst === sel) && e.type !== 'member') : []
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Fragment relationship graph</h1>
        <p className="text-sm text-muted">{g.nodes.length} nodes · {g.edges.length} relationships. Edges are <DataClass kind="inferred" /> probabilities from evidence, not facts. Click nodes or edges for their evidence.</p>
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <label className="text-muted">Hide links below <span className="font-mono text-ink">{minConf}%</span>
            <input type="range" min={0} max={95} step={5} value={minConf} onChange={e => setMinConf(+e.target.value)} className="ml-2 w-32 align-middle accent-teal" /></label>
          <label className="flex items-center gap-1.5 text-ink2"><input type="checkbox" checked={alts} onChange={e => setAlts(e.target.checked)} />Show rejected alternatives</label>
          <select value={focus ?? ''} onChange={e => { const v = e.target.value; setParams(v ? { focus: v } : {}) }}
            className="rounded-lg border border-line2 bg-soft px-2 py-1.5 text-ink">
            <option value="">Highlight a reconstruction group…</option>
            {files.map(f => <option key={f.id} value={f.id}>{f.id} · {f.label}</option>)}
          </select>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(EDGE_STYLE).map(([k, v]) => (
              <button key={k} onClick={() => { const s = new Set(types); if (s.has(k)) s.delete(k); else s.add(k); setTypes(s) }}
                className={`rounded-full px-2 py-0.5 ring-1 ${types.has(k) ? 'text-ink ring-slate-500' : 'text-muted/70 ring-line2 line-through'}`}
                style={types.has(k) ? { boxShadow: `inset 0 -2px 0 ${v.color}` } : undefined}>{k.replace('_', ' ')}</button>
            ))}
          </div>
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <div>
          <RelGraph graph={g} minConf={minConf} showAlternatives={alts} focus={focus} selected={sel} onSelect={setSel} height={680} types={types} />
          <div className="mt-2"><EdgeLegend /></div>
        </div>
        <Card title="Inspector" className="self-start">
          {!sel && <p className="text-sm text-muted">Select a node or an edge.</p>}
          {selNode && (
            <div className="space-y-2 text-sm">
              <div className="font-mono text-xs text-muted">{selNode.kind.toUpperCase()} · {selNode.id}</div>
              <div className="font-medium text-ink">{selNode.label}</div>
              {selNode.status && <StatusBadge status={selNode.status} />}
              {selNode.kind === 'file' && <Link to={`../files/${selNode.id}`} className="block text-xs text-brand hover:underline">Open file analysis →</Link>}
              {selNode.kind === 'fragment' && <div className="space-y-1 font-mono text-xs text-muted">
                <div>family: {selNode.type}</div><div>offset: {selNode.offset}</div><div>length: {bytes(selNode.length)}</div>
                <div>group: {selNode.group ?? 'unassigned'}</div>
                <Link to={`../fragments?q=${selNode.id}`} className="text-brand hover:underline">Inspect bytes →</Link></div>}
              {selNode.kind === 'metadata' && selNode.detail && <div className="space-y-1 font-mono text-xs text-muted">
                <div>name: {selNode.detail.name}</div><div>first cluster: {selNode.detail.first_cluster}</div>
                <div>size: {bytes(selNode.detail.size)}</div><div>modified: {selNode.detail.modified}</div></div>}
              {related.length > 0 && <div className="border-t border-line pt-2">
                <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">Relationships</div>
                {related.map((e, i) => <button key={i} onClick={() => setSel(`edge:${e.src}>${e.dst}`)} className="block w-full truncate rounded px-1 py-0.5 text-left text-xs text-ink2 hover:bg-tealsoft">
                  {e.src} → {e.dst} · {e.type} · <span className="font-mono">{e.confidence.toFixed(0)}%</span>{!e.chosen && ' (rejected)'}</button>)}
              </div>}
            </div>
          )}
          {selEdge && <EdgeDetail e={selEdge} />}
        </Card>
      </div>
    </div>
  )
}

export function EdgeDetail({ e }: { e: EdgeT }) {
  return (
    <div className="space-y-2 text-sm">
      <div className="font-mono text-xs text-muted">{e.type.toUpperCase()} {e.chosen ? '' : '· REJECTED ALTERNATIVE'}</div>
      <div className="font-medium text-ink">{e.src} → {e.dst}</div>
      <div className="flex items-baseline gap-2"><span className="font-mono text-2xl text-brand">{e.confidence.toFixed(1)}%</span><span className="text-xs text-muted">{relLabel(e.confidence)} relationship</span></div>
      <ul className="list-disc space-y-1 pl-4 text-xs text-ink2">{e.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul>
      {e.contributions && e.contributions.length > 0 && (
        <div className="border-t border-line pt-2">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-muted">Model evidence (feature contributions, log-odds)</div>
          {e.contributions.map(ct => (
            <div key={ct.feature} className="grid grid-cols-[1fr_44px_70px] items-center gap-2 py-0.5 text-[11px]">
              <span className="truncate text-muted" title={ct.label}>{ct.label}</span>
              <span className="text-right font-mono text-muted">{ct.value.toFixed(2)}</span>
              <div className="relative h-2 rounded bg-track">
                <div className="absolute top-0 h-2 rounded" style={{ left: ct.contribution >= 0 ? '50%' : `${50 + Math.max(-50, ct.contribution * 10)}%`, width: `${Math.min(50, Math.abs(ct.contribution) * 10)}%`, background: ct.contribution >= 0 ? '#3987e5' : '#e66767' }} />
                <div className="absolute left-1/2 top-[-2px] h-3 w-px bg-slate-500" />
              </div>
            </div>
          ))}
          <p className="mt-1.5 text-[10px] text-muted">Blue pushes toward "belongs together", red against. A probability, not proof.</p>
        </div>
      )}
    </div>
  )
}
