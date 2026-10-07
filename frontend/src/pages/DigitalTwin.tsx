import { useEffect, useMemo, useRef, useState } from 'react'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { motion } from 'framer-motion'
import { Boxes, HardDrive, Pause, Play, Workflow } from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { adv, type Twin, type TwinNode } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import FilePicker, { useFileParam } from '../components/FilePicker'
import { Card, CheckIcon, DataClassFrom, ErrorBox, PageHeader, Spinner } from '../components/ui'

const KIND: Record<string, { bg: string; border: string; text: string; dashed?: boolean }> = {
  image: { bg: '#0f4c5c', border: '#0f4c5c', text: '#fff' },
  fragment: { bg: '#e4f3f2', border: '#1fa6a6', text: '#17323a' },
  corrupted: { bg: '#fff1e6', border: '#e07b39', text: '#7a3310' },
  missing: { bg: '#fdecec', border: '#d9534f', text: '#8a1f1c', dashed: true },
  operation: { bg: '#fff8ea', border: '#f4a340', text: '#17323a' },
  validation: { bg: '#eef5fb', border: '#3282b8', text: '#17323a' },
  security: { bg: '#f3effb', border: '#7a5bc7', text: '#17323a' },
  artifact: { bg: '#e9f7ef', border: '#22a06b', text: '#17323a' },
}

type TData = { n: TwinNode; dim: boolean; active: boolean; selected: boolean }

function TwinNodeView({ data }: NodeProps<Node<TData>>) {
  const k = KIND[data.n.kind] ?? KIND.fragment
  const d = data.n.detail as Record<string, unknown>
  const sub = data.n.kind === 'fragment' || data.n.kind === 'corrupted' ? `${d.source_offset ?? ''} · ${Number(d.length ?? 0).toLocaleString()} B`
    : data.n.kind === 'missing' ? `${Number(d.length ?? 0).toLocaleString()} B not located`
    : data.n.kind === 'image' ? String(d.name ?? '') : data.n.kind === 'validation' ? String(d.summary ?? '')
    : data.n.kind === 'security' ? String(d.label ?? '') : data.n.kind === 'artifact' ? String(d.name ?? '') : `${(d.operations as unknown[] | undefined)?.length ?? 0} operation(s)`
  return (
    <motion.div animate={{ opacity: data.dim ? 0.2 : 1, scale: data.active ? 1.06 : 1 }} transition={{ duration: 0.3 }}
      className="w-[170px] rounded-xl px-3 py-2 text-left shadow-md"
      style={{ background: k.bg, color: k.text, border: `2px ${k.dashed ? 'dashed' : 'solid'} ${data.selected ? '#17323a' : k.border}` }}>
      <Handle type="target" position={Position.Top} className="!border-0 !bg-transparent" />
      <div className="truncate font-mono text-[12px] font-bold">{data.n.label}</div>
      <div className="truncate text-[10px] opacity-80">{sub}</div>
      <Handle type="source" position={Position.Bottom} className="!border-0 !bg-transparent" />
      <Handle type="source" id="r" position={Position.Right} className="!border-0 !bg-transparent" />
      <Handle type="target" id="l" position={Position.Left} className="!border-0 !bg-transparent" />
    </motion.div>
  )
}
const nodeTypes = { twin: TwinNodeView }

function Detail({ n }: { n: TwinNode }) {
  const d = n.detail as Record<string, unknown>
  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center gap-2"><span className="font-mono text-sm font-bold text-ink">{n.label}</span><DataClassFrom text={n.data_class} /></div>
      {Object.entries(d).map(([k, v]) => {
        if (v == null || v === '') return null
        if (k === 'checks' && Array.isArray(v)) {
          return <div key={k}><div className="font-semibold uppercase tracking-wider text-muted">validator checks</div>
            <ul className="mt-1 space-y-0.5">{(v as { name: string; status: string; detail: string }[]).map(c => (
              <li key={c.name} className="flex gap-1.5"><CheckIcon status={c.status} /><span><b>{c.name}</b> <span className="text-muted">{String(c.detail ?? '').slice(0, 140)}</span></span></li>))}</ul></div>
        }
        if (Array.isArray(v)) {
          return <div key={k}><div className="font-semibold uppercase tracking-wider text-muted">{k.replaceAll('_', ' ')}</div>
            <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-ink2">{v.map((x, i) => <li key={i}>{String(x)}</li>)}</ul></div>
        }
        return <div key={k} className="grid grid-cols-[120px_1fr] gap-2"><span className="font-semibold uppercase tracking-wider text-muted">{k.replaceAll('_', ' ')}</span>
          <span className="break-all text-ink">{typeof v === 'boolean' ? (v ? 'yes' : 'no') : typeof v === 'number' ? v.toLocaleString() : String(v)}</span></div>
      })}
    </div>
  )
}

export default function DigitalTwin() {
  const { c } = useOutletContext<CaseCtx>()
  const { files, fid, set } = useFileParam(c.id, fs => [...fs].sort((a, b) => (b.missing_ranges ? 1 : 0) - (a.missing_ranges ? 1 : 0) || b.fragment_count - a.fragment_count)[0]?.file_id)
  const [twin, setTwin] = useState<Twin | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [step, setStep] = useState<number | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => { if (fid) { setTwin(null); setErr(null); setSel(null); setStep(null); adv.twin(c.id, fid).then(setTwin).catch(setErr) } }, [c.id, fid])
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  const order = useMemo(() => twin ? ['image', ...twin.nodes.filter(n => n.id.startsWith('seg')).map(n => n.id), 'recon', 'validate', 'security', 'artifact'] : [], [twin])
  const play = () => {
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; setStep(null); return }
    setStep(0)
    timer.current = window.setInterval(() => setStep(s => {
      if (s == null || s >= order.length - 1) { if (timer.current) window.clearInterval(timer.current); timer.current = null; return null }
      return s + 1
    }), 900)
  }
  const { nodes, edges } = useMemo(() => {
    if (!twin) return { nodes: [] as Node<TData>[], edges: [] as Edge[] }
    const segs = twin.nodes.filter(n => n.id.startsWith('seg'))
    const W = 200
    const cols = Math.min(6, Math.max(1, segs.length))
    const rows = Math.ceil(segs.length / cols)
    const width = Math.max(cols, 4) * W
    const pos: Record<string, { x: number; y: number }> = { image: { x: width / 2 - 85, y: 0 } }
    // logical order reads left→right, then wraps (serpentine rows keep adjacent segments adjacent)
    segs.forEach((n, i) => {
      const r = Math.floor(i / cols)
      const c = r % 2 === 0 ? i % cols : cols - 1 - (i % cols)
      pos[n.id] = { x: c * W, y: 160 + r * 120 }
    })
    const py = 160 + rows * 120 + 60
    ;['recon', 'validate', 'security', 'artifact'].forEach((id, i) => { pos[id] = { x: width / 2 - 85 + (i - 1.5) * 230, y: py + (i % 2) * 40 } })
    const activeIdx = step == null ? order.length : step
    const ns: Node<TData>[] = twin.nodes.map(n => ({
      id: n.id, type: 'twin', position: pos[n.id] ?? { x: 0, y: 0 },
      data: { n, dim: order.indexOf(n.id) > activeIdx, active: step != null && order[step] === n.id, selected: sel === n.id },
    }))
    const color: Record<string, string> = { source: '#0f4c5c', order: '#1fa6a6', assemble: '#dccdb1', pipeline: '#f4a340' }
    const es: Edge[] = twin.edges.map(e => ({
      id: e.id, source: e.src, target: e.dst, label: e.kind === 'assemble' ? undefined : e.label,
      sourceHandle: e.kind === 'order' ? 'r' : undefined, targetHandle: e.kind === 'order' ? 'l' : undefined,
      animated: e.kind === 'pipeline' || e.kind === 'source',
      style: { stroke: color[e.kind] ?? '#8a9aa0', strokeWidth: e.kind === 'assemble' ? 1 : 2, strokeDasharray: e.label === 'gap' ? '5 4' : undefined,
        opacity: Math.max(order.indexOf(e.src), order.indexOf(e.dst)) > activeIdx ? 0.15 : 1 },
      labelStyle: { fill: '#17323a', fontSize: 10, fontWeight: 600 }, labelBgStyle: { fill: '#fff8ea', fillOpacity: 0.95 },
      markerEnd: e.kind === 'assemble' ? undefined : { type: MarkerType.ArrowClosed, color: color[e.kind] ?? '#8a9aa0' },
    }))
    return { nodes: ns, edges: es }
  }, [twin, step, sel, order])

  const current = step != null ? order[step] : sel
  const decision = twin?.decisions.find(d => d.to === current)
  const selNode = twin?.nodes.find(n => n.id === (sel ?? current))
  return (
    <div className="space-y-4">
      <PageHeader icon={Workflow} accent="#1fa6a6" title="Recovery Digital Twin"
        subtitle="The reconstruction history of one artifact: where every byte came from on the storage image, how the fragments were ordered, which ranges are missing or corrupted, and how the result was validated and scanned. Explanations are assembled from recorded evidence."
        action={files && <FilePicker files={files} value={fid} onChange={set} />} />
      {err != null && <ErrorBox title="Digital twin unavailable" error={err} />}
      {!twin && !err && <Spinner />}
      {twin && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <Card className="overflow-hidden" bodyClass="p-0" title={<span>{twin.file_name}</span>}
            subtitle="Zoom and pan the graph; click any node for its metadata, offsets, hashes and decisions."
            action={<button onClick={play} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-deep">
              {step != null ? <><Pause className="h-3.5 w-3.5" />Stop</> : <><Play className="h-3.5 w-3.5" />Replay reconstruction</>}</button>}>
            <div className="h-[560px]">
              <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.2} onNodeClick={(_, n) => setSel(n.id)} proOptions={{ hideAttribution: true }}>
                <Background color="#e3d7c0" gap={22} />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
            <div className="flex flex-wrap gap-3 border-t border-line px-4 py-2 text-[11px] text-muted">
              {Object.entries({ image: 'storage image', fragment: 'observed fragment', corrupted: 'corrupted range', missing: 'missing range', operation: 'reconstruction',
                validation: 'validation', security: 'security', artifact: 'final artifact' }).map(([k, l]) => (
                <span key={k} className="inline-flex items-center gap-1"><span className="h-3 w-3 rounded" style={{ background: KIND[k].bg, border: `2px ${KIND[k].dashed ? 'dashed' : 'solid'} ${KIND[k].border}` }} />{l}</span>))}
            </div>
          </Card>
          <div className="space-y-4">
            <Card icon={Boxes} title="Selected step">
              {/* content switches immediately; the slide-in is decoration only (never gates what is shown) */}
              <motion.div key={current ?? 'none'} initial={{ x: 12 }} animate={{ x: 0 }} transition={{ duration: 0.2 }}>
                {decision && <div className="mb-3 rounded-xl border border-teal/30 bg-tealsoft p-2.5 text-xs text-ink"><div className="mb-0.5 text-[10px] font-bold uppercase tracking-wider text-brand">Reconstruction decision</div>{decision.text}</div>}
                {selNode ? <Detail n={selNode} /> : <p className="text-xs text-muted">Click a node, or replay the reconstruction.</p>}
              </motion.div>
            </Card>
            <Card icon={HardDrive} title="Reconstruction decisions" subtitle={`${twin.decisions.length} ordering decision(s)`}>
              <ol className="max-h-[260px] space-y-2 overflow-y-auto pr-1 text-xs scroll-thin">
                {twin.decisions.map((d, i) => (
                  <li key={i}><button onClick={() => setSel(d.to)} className={`w-full rounded-lg p-2 text-left hover:bg-tealsoft ${sel === d.to ? 'bg-tealsoft' : 'bg-soft'}`}>
                    <span className="font-mono text-[10px] text-muted">#{i + 1} {d.type}{d.confidence != null ? ` · ${d.confidence}%` : ''}</span><div className="text-ink2">{d.text}</div></button></li>
                ))}
                {!twin.decisions.length && <li className="text-muted">Single contiguous source: no ordering decisions were needed.</li>}
              </ol>
            </Card>
          </div>
          <Card title="Timeline" subtitle="Audit-trail entries for this artifact" className="xl:col-span-2">
            <ol className="relative ml-2 border-l-2 border-line pl-4">
              {twin.timeline.map((t, i) => (
                <motion.li key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i * 0.03, 0.6) }} className="relative mb-2 text-xs">
                  <span className="absolute -left-[23px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-teal" />
                  <span className="font-mono text-[10px] text-muted">{t.ts.slice(11, 19)} · {t.stage}</span><div className="text-ink2">{t.message}</div>
                </motion.li>
              ))}
            </ol>
            <Link to={`../files/${twin.file_id}`} className="text-xs font-semibold text-brand hover:underline">Open full file analysis →</Link>
          </Card>
        </div>
      )}
    </div>
  )
}
