import { useEffect, useMemo, useState } from 'react'
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Share2 } from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { adv, type CrossArtifact as CA, type XRel } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, DataClass, Empty, ErrorBox, Hint, PageHeader, Spinner, Tag } from '../components/ui'
import { TYPE_COLOR, niceType } from '../lib/format'

export const REL_COLOR: Record<string, string> = {
  hash_duplicate: '#e34948', container: '#22a06b', references: '#0f4c5c', shared_identifier: '#3282b8', shared_metadata: '#f4a340',
  directory: '#8a9aa0', timestamp: '#a9b5ba', filename: '#e87ba4', content: '#7a5bc7', database: '#eda100', storage: '#c9bfae',
}

type AData = { name: string; type: string; id: string; dim: boolean; degree: number }
function ArtifactNode({ data }: NodeProps<Node<AData>>) {
  return (
    <div className={`w-[170px] rounded-xl border-2 bg-card px-3 py-2 text-left shadow-md transition ${data.dim ? 'opacity-25' : ''}`} style={{ borderColor: TYPE_COLOR[data.type] ?? '#6b7c83' }}>
      <Handle type="target" position={Position.Top} className="!border-0 !bg-transparent" style={{ top: '50%' }} />
      <div className="truncate text-[12px] font-semibold text-ink" title={data.name}>{data.name}</div>
      <div className="text-[10px] text-muted">{data.id} · {niceType(data.type)} · {data.degree} link(s)</div>
      <Handle type="source" position={Position.Bottom} className="!border-0 !bg-transparent" style={{ top: '50%' }} />
    </div>
  )
}
const nodeTypes = { art: ArtifactNode }

export default function CrossArtifact() {
  const { c } = useOutletContext<CaseCtx>()
  const [d, setD] = useState<CA | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [types, setTypes] = useState<Set<string>>(new Set())
  const [minConf, setMinConf] = useState(30)
  const [artType, setArtType] = useState('')
  const [sel, setSel] = useState<XRel | null>(null)
  useEffect(() => { adv.cross(c.id).then(r => { setD(r); setTypes(new Set(Object.keys(r.types).filter(t => t !== 'storage'))) }).catch(setErr) }, [c.id])
  const rels = useMemo(() => d ? d.relationships.filter(r => types.has(r.type) && r.confidence >= minConf &&
    (!artType || d.nodes.find(n => n.id === r.source)?.type === artType || d.nodes.find(n => n.id === r.target)?.type === artType)) : [], [d, types, minConf, artType])
  const { nodes, edges } = useMemo(() => {
    if (!d) return { nodes: [] as Node<AData>[], edges: [] as Edge[] }
    const used = new Set(rels.flatMap(r => [r.source, r.target]))
    const list = d.nodes.filter(n => !artType || n.type === artType || used.has(n.id))
    const R = Math.max(260, list.length * 42)
    const ns: Node<AData>[] = list.map((n, i) => {
      const a = (2 * Math.PI * i) / Math.max(1, list.length)
      return { id: n.id, type: 'art', position: { x: R * Math.cos(a), y: R * Math.sin(a) },
        data: { name: n.name, type: n.type, id: n.id, dim: !used.has(n.id), degree: rels.filter(r => r.source === n.id || r.target === n.id).length } }
    })
    const es: Edge[] = rels.map((r, i) => ({
      id: `${r.type}-${r.source}-${r.target}-${i}`, source: r.source, target: r.target, type: 'straight',
      style: { stroke: REL_COLOR[r.type] ?? '#8a9aa0', strokeWidth: 1 + (r.confidence / 100) * 3, opacity: sel && sel !== r ? 0.25 : 0.85,
        strokeDasharray: r.confidence < 55 ? '5 4' : undefined },
      label: sel === r ? `${r.type_label} ${r.confidence}%` : undefined, labelStyle: { fontSize: 10, fill: '#17323a' },
      labelBgStyle: { fill: '#fff8ea' }, data: { rel: r },
    }))
    return { nodes: ns, edges: es }
  }, [d, rels, artType, sel])
  if (err) return <ErrorBox error={err} />
  if (!d) return <Spinner />
  const artTypes = [...new Set(d.nodes.map(n => n.type))]
  return (
    <div className="space-y-4">
      <PageHeader icon={Share2} accent="#3282b8" title="Cross-Artifact Intelligence"
        subtitle="Evidence-based relationships between recovered artifacts: shared hashes, container membership, referenced filenames, shared identifiers and metadata, timestamps, similar names, database values, content similarity and storage adjacency. Every link is an INFERRED RELATIONSHIP with its evidence." />
      <Card title="Filters" bodyClass="p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {Object.entries(d.types).map(([k, l]) => {
            const n = d.relationships.filter(r => r.type === k).length
            const on = types.has(k)
            return (
              <button key={k} onClick={() => { const s = new Set(types); if (on) s.delete(k); else s.add(k); setTypes(s) }}
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset transition ${on ? 'bg-card text-ink ring-line2' : 'bg-track text-muted ring-transparent line-through'}`}>
                <span className="h-2 w-2 rounded-full" style={{ background: REL_COLOR[k] }} />{l} <span className="text-muted">{n}</span>
              </button>
            )
          })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-muted">
          <label>Confidence ≥ <span className="font-mono text-ink">{minConf}%</span>
            <input type="range" min={0} max={100} step={5} value={minConf} onChange={e => setMinConf(+e.target.value)} className="ml-2 w-40 align-middle accent-teal" /></label>
          <label>Artifact type <select value={artType} onChange={e => setArtType(e.target.value)} className="ml-1 rounded-lg border border-line2 bg-soft px-2 py-1 text-xs text-ink">
            <option value="">All</option>{artTypes.map(t => <option key={t} value={t}>{niceType(t)}</option>)}</select></label>
          <span className="ml-auto">{rels.length} of {d.relationships.length} relationship(s) shown · {d.engine}</span>
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card title="Artifact Relationship Graph" bodyClass="p-0" className="overflow-hidden">
          <div className="h-[540px]">
            {nodes.length ? (
              <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.2} proOptions={{ hideAttribution: true }}
                onEdgeClick={(_, e) => setSel((e.data as { rel: XRel }).rel)} onPaneClick={() => setSel(null)}>
                <Background color="#e3d7c0" gap={22} /><Controls showInteractive={false} />
              </ReactFlow>
            ) : <div className="p-6"><Empty title="INSUFFICIENT EVIDENCE">Fewer than two artifacts: no cross-artifact relationships can be evaluated.</Empty></div>}
          </div>
          <p className="border-t border-line px-4 py-2 text-[11px] text-muted">Line width ∝ confidence; dashed = weak (&lt; 55%). Click a line to see its evidence.</p>
        </Card>
        <div className="space-y-4">
          <Card title="Selected relationship">
            {sel ? (
              <div className="space-y-2 text-xs">
                <div className="flex flex-wrap items-center gap-2"><Tag tone="text-ink bg-card ring-line2"><span className="h-2 w-2 rounded-full" style={{ background: REL_COLOR[sel.type] }} />{sel.type_label}</Tag><DataClass kind="inferred" /></div>
                <div><span className="text-muted">Source:</span> <Link className="font-semibold text-brand hover:underline" to={`../files/${sel.source}`}>{sel.source_name}</Link></div>
                <div><span className="text-muted">Target:</span> <Link className="font-semibold text-brand hover:underline" to={`../files/${sel.target}`}>{sel.target_name}</Link></div>
                <div><span className="text-muted">Confidence:</span> <b>{sel.confidence}%</b> ({sel.label})</div>
                <div><div className="text-muted">Evidence</div><ul className="list-disc pl-4 text-ink2">{sel.evidence.map(e => <li key={e}>{e}</li>)}</ul></div>
              </div>
            ) : <p className="text-xs text-muted">Click a relationship line in the graph or a row below.</p>}
          </Card>
          <Card title="Most connected artifacts">
            <ul className="space-y-1 text-xs">{d.most_connected.map(m => <li key={m.id} className="flex justify-between"><span className="truncate">{m.name}</span><span className="font-mono text-muted">{m.centrality}</span></li>)}</ul>
            <p className="mt-2 text-[11px] text-muted">{d.clusters.length} connected group(s) of related artifacts (degree centrality via NetworkX).</p>
          </Card>
          <Hint>{d.caveat}</Hint>
        </div>
      </div>
      <Card title="Relationships" bodyClass="p-0">
        <div className="max-h-[420px] overflow-auto scroll-thin"><table className="w-full min-w-[820px] text-xs">
          <thead className="sticky top-0 bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr><th className="px-4 py-2">Type</th><th>Source artifact</th><th>Target artifact</th><th>Confidence</th><th>Evidence</th></tr></thead>
          <tbody>{rels.map((r, i) => (
            <tr key={i} onClick={() => setSel(r)} className={`cursor-pointer border-t border-line align-top hover:bg-tealsoft ${sel === r ? 'bg-tealsoft' : ''}`}>
              <td className="px-4 py-1.5"><span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: REL_COLOR[r.type] }} />{r.type_label}</span></td>
              <td className="pr-2">{r.source_name}</td><td className="pr-2">{r.target_name}</td>
              <td className="pr-2 font-mono">{r.confidence}% <span className="text-muted">{r.label}</span></td><td className="pr-4 text-ink2">{r.evidence[0]}</td>
            </tr>))}</tbody></table></div>
      </Card>
    </div>
  )
}
