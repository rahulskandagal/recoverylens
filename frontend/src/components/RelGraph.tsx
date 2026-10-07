import { useMemo } from 'react'
import { Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { EdgeT, Graph, GraphNode } from '../api'
import { FAMILY_COLOR, STATUS_META, TYPE_COLOR, relLabel } from '../lib/format'

export const EDGE_STYLE: Record<string, { color: string; dash?: string; label: string }> = {
  continuation: { color: '#1fa6a6', label: 'Possible continuation' },
  structural: { color: '#1fa6a6', dash: '6 4', label: 'Structural (same index, gap between)' },
  metadata: { color: '#f4a340', dash: '2 4', label: 'Metadata relationship' },
  content_similarity: { color: '#7a5bc7', dash: '8 4', label: 'Content similarity' },
  temporal: { color: '#8a9aa0', dash: '3 5', label: 'Temporal relationship' },
  container: { color: '#22a06b', label: 'Container relationship' },
  duplicate: { color: '#e87ba4', dash: '4 3', label: 'Duplicate copy' },
  member: { color: '#c9bfae', label: 'Member of reconstruction' },
}

type NData = { g: GraphNode; dim: boolean; selected: boolean }

function FileNode({ data }: NodeProps<Node<NData>>) {
  const g = data.g
  const st = g.status ? STATUS_META[g.status] : null
  return (
    <div className={`w-[200px] rounded-lg border-2 bg-soft px-3 py-2 text-left shadow-lg transition ${data.dim ? 'opacity-25' : ''}`}
      style={{ borderColor: data.selected ? '#0f4c5c' : TYPE_COLOR[g.type] ?? '#6b7c83' }}>
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0" />
      <div className="truncate text-[12px] font-semibold text-ink">{g.label}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted">
        <span className="font-mono">{g.id}</span>
        {st && <span style={{ color: st.color }}>{st.icon} {st.label}</span>}
      </div>
      {g.integrity != null && <div className="mt-1 h-1 rounded bg-track"><div className="h-full rounded" style={{ width: `${g.integrity}%`, background: '#1fa6a6' }} /></div>}
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0" />
    </div>
  )
}

function FragNode({ data }: NodeProps<Node<NData>>) {
  const g = data.g
  const color = g.kind === 'metadata' ? '#f4a340' : g.kind === 'embedded' ? '#22a06b' : FAMILY_COLOR[g.type] ?? '#6b7c83'
  return (
    <div className={`w-[118px] rounded-md border bg-card px-2 py-1.5 text-left transition ${data.dim ? 'opacity-25' : ''}`}
      style={{ borderColor: data.selected ? '#0f4c5c' : color }}>
      <Handle type="target" position={Position.Left} className="!bg-transparent !border-0" />
      <div className="flex items-center gap-1 font-mono text-[11px] font-semibold text-ink">
        {g.kind === 'fragment' && g.header && <span className="rounded bg-teal/20 px-1 text-[9px] text-brand">HDR</span>}
        <span className="truncate">{g.label}</span>
      </div>
      <div className="truncate text-[9px] text-muted">{g.kind === 'fragment' ? `${g.type} · ${g.offset}` : g.kind === 'metadata' ? 'deleted dir entry' : `embedded · ${g.status}`}</div>
      <Handle type="source" position={Position.Right} className="!bg-transparent !border-0" />
      <Handle type="target" id="t" position={Position.Top} className="!bg-transparent !border-0" />
      <Handle type="source" id="b" position={Position.Bottom} className="!bg-transparent !border-0" />
    </div>
  )
}

const nodeTypes = { file: FileNode, frag: FragNode }

export function layout(graph: Graph, focus?: string | null) {
  const groups = new Map<string, GraphNode[]>()
  const loose: GraphNode[] = []
  for (const n of graph.nodes) {
    if (n.kind === 'file') continue
    if (n.group) { if (!groups.has(n.group)) groups.set(n.group, []); groups.get(n.group)!.push(n) } else loose.push(n)
  }
  // order fragments inside a group along chosen continuation edges
  const pos: Record<string, { x: number; y: number }> = {}
  const files = graph.nodes.filter(n => n.kind === 'file')
  const maxW = 1700
  let x = 0, y = 0, rowH = 0
  for (const f of files) {
    const members = groups.get(f.id) ?? []
    const frags = members.filter(m => m.kind === 'fragment')
    const others = members.filter(m => m.kind !== 'fragment')
    const w = Math.max(230, frags.length * 136 + 20)
    if (x + w > maxW && x > 0) { x = 0; y += rowH + 70; rowH = 0 }
    pos[f.id] = { x: x + w / 2 - 100, y }
    frags.forEach((m, i) => { pos[m.id] = { x: x + 10 + i * 136, y: y + 110 } })
    others.forEach((m, i) => { pos[m.id] = { x: x + 10 + i * 136, y: y + 175 } })
    const h = others.length ? 220 : 160
    rowH = Math.max(rowH, h)
    x += w + 40
  }
  y += rowH + 70
  loose.forEach((m, i) => { pos[m.id] = { x: (i % 12) * 136, y: y + Math.floor(i / 12) * 60 } })
  void focus
  return pos
}

export default function RelGraph({ graph, minConf = 0, showAlternatives = true, focus, selected, onSelect, height = 640, types }: {
  graph: Graph; minConf?: number; showAlternatives?: boolean; focus?: string | null; selected?: string | null
  onSelect?: (id: string | null) => void; height?: number; types?: Set<string>
}) {
  const pos = useMemo(() => layout(graph, focus), [graph, focus])
  const focusSet = useMemo(() => {
    if (!focus) return null
    const s = new Set<string>([focus])
    graph.nodes.forEach(n => { if (n.group === focus) s.add(n.id) })
    graph.edges.forEach(e => { if (e.src === focus || e.dst === focus) { s.add(e.src); s.add(e.dst) } })
    return s
  }, [graph, focus])
  const nodes: Node<NData>[] = graph.nodes.filter(n => pos[n.id]).map(n => ({
    id: n.id, type: n.kind === 'file' ? 'file' : 'frag', position: pos[n.id],
    data: { g: n, dim: !!focusSet && !focusSet.has(n.id), selected: selected === n.id },
  }))
  const edges: Edge[] = graph.edges
    .filter((e: EdgeT) => e.confidence >= minConf && (showAlternatives || e.chosen) && (!types || types.has(e.type)) && pos[e.src] && pos[e.dst])
    .map((e, i) => {
      const st = EDGE_STYLE[e.type] ?? EDGE_STYLE.member
      const alt = !e.chosen
      const dim = focusSet && !(focusSet.has(e.src) && focusSet.has(e.dst))
      const color = alt ? '#d9534f' : st.color
      return {
        id: `${e.src}-${e.dst}-${e.type}-${i}`, source: e.src, target: e.dst,
        sourceHandle: e.type === 'member' ? 'b' : undefined, targetHandle: undefined,
        label: e.type === 'member' ? undefined : `${e.confidence.toFixed(0)}%${alt ? ' (rejected)' : ''}`,
        labelStyle: { fill: '#17323a', fontSize: 10, fontFamily: 'ui-monospace' },
        labelBgStyle: { fill: '#fff8ea', fillOpacity: 0.95 },
        style: { stroke: color, strokeWidth: e.type === 'member' ? 1 : 1.5 + (e.confidence / 100) * 1.5, strokeDasharray: alt ? '3 4' : st.dash, opacity: dim ? 0.1 : alt ? 0.6 : 1 },
        markerEnd: e.type === 'member' ? undefined : { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        animated: e.type === 'continuation' && e.chosen && e.confidence >= 90 && !dim,
        data: { e },
      }
    })
  return (
    <div style={{ height }} className="overflow-hidden rounded-lg border border-line bg-canvas">
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.1} maxZoom={2}
        onNodeClick={(_, n) => onSelect?.(n.id)} onPaneClick={() => onSelect?.(null)}
        onEdgeClick={(_, ed) => onSelect?.(`edge:${(ed.data as { e: EdgeT }).e.src}>${(ed.data as { e: EdgeT }).e.dst}`)}
        nodesDraggable proOptions={{ hideAttribution: true }}>
        <Background color="#e3d7c0" gap={22} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={n => { const g = (n.data as NData).g; return g.kind === 'file' ? TYPE_COLOR[g.type] ?? '#6b7c83' : '#c9bfae' }} maskColor="rgba(255,248,234,0.7)" style={{ background: '#ffffff', border: '1px solid #ecdfc8', borderRadius: 10 }} />
      </ReactFlow>
    </div>
  )
}

export function EdgeLegend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-muted">
      {Object.entries(EDGE_STYLE).map(([k, v]) => (
        <span key={k} className="flex items-center gap-1.5">
          <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={v.color} strokeWidth="2" strokeDasharray={v.dash} /></svg>{v.label}
        </span>
      ))}
      <span className="flex items-center gap-1.5"><svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke="#d9534f" strokeWidth="2" strokeDasharray="3 4" /></svg>Rejected alternative</span>
      <span className="text-muted">Labels: {['Strong ≥90', 'Probable ≥75', 'Possible ≥55', 'Weak ≥35'].join(' · ')}</span>
    </div>
  )
}

export { relLabel }
