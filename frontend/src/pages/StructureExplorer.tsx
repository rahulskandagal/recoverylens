import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, ListTree } from 'lucide-react'
import { motion } from 'framer-motion'
import { useOutletContext } from 'react-router-dom'
import { adv, type StructNode, type StructureResult } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import FilePicker, { useFileParam } from '../components/FilePicker'
import { Card, CheckIcon, DataClass, Empty, ErrorBox, Hint, PageHeader, Spinner, Tag } from '../components/ui'
import { bytes } from '../lib/format'

const STATE_TONE: Record<string, string> = {
  recovered: 'text-emerald-800 bg-emerald-50 ring-emerald-300', missing: 'text-red-800 bg-red-50 ring-red-300',
  corrupted: 'text-orange-900 bg-orange-50 ring-orange-300', partial: 'text-amber-900 bg-amber-50 ring-amber-300',
}
const STATE_COLOR: Record<string, string> = { recovered: '#22a06b', missing: '#d9534f', corrupted: '#e07b39', partial: '#f4a340' }

function TreeRow({ n, depth, onPick, picked }: { n: StructNode; depth: number; onPick: (n: StructNode) => void; picked: StructNode | null }) {
  const [open, setOpen] = useState(depth < 1 || n.kind === 'group' && n.children.length <= 8)
  const hasKids = n.children.length > 0
  return (
    <>
      <div onClick={() => onPick(n)} className={`grid cursor-pointer grid-cols-[minmax(220px,1.6fr)_110px_90px_90px_100px_1fr] items-center gap-2 border-b border-line px-3 py-1.5 text-xs hover:bg-tealsoft ${picked === n ? 'bg-tealsoft' : ''}`}>
        <div className="flex min-w-0 items-center gap-1" style={{ paddingLeft: depth * 16 }}>
          {hasKids ? <button onClick={e => { e.stopPropagation(); setOpen(!open) }} className="text-muted">{open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</button> : <span className="w-3.5" />}
          <CheckIcon status={n.validation} />
          <span className={`truncate ${n.kind === 'group' ? 'font-semibold text-ink' : 'text-ink2'}`}>{n.name}</span>
        </div>
        <span className="font-mono text-[11px] text-muted">{n.offset_hex ?? '—'}</span>
        <span className="font-mono text-[11px] text-muted">{n.offset == null ? '' : bytes(n.length)}</span>
        <span>{n.offset != null && <Tag tone={STATE_TONE[n.state]}>{n.state}</Tag>}</span>
        <span className="font-mono text-[11px]">{n.offset != null && `${n.confidence}%`}</span>
        <span className="truncate font-mono text-[11px] text-muted">{n.offset != null ? n.source_fragments.join(', ') : n.detail}</span>
      </div>
      {open && n.children.map((k, i) => <TreeRow key={i} n={k} depth={depth + 1} onPick={onPick} picked={picked} />)}
    </>
  )
}

function flat(ns: StructNode[]): StructNode[] {
  return ns.flatMap(n => [...(n.kind === 'group' || n.offset == null ? [] : [n]), ...flat(n.children)])
}

export default function StructureExplorer() {
  const { c } = useOutletContext<CaseCtx>()
  const { files, fid, set } = useFileParam(c.id, fs => fs.find(f => !f.orphan && ['pdf', 'docx', 'jpeg', 'sqlite', 'zip', 'mp4', 'png'].includes(f.file_type))?.file_id)
  const [r, setR] = useState<StructureResult | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [picked, setPicked] = useState<StructNode | null>(null)
  useEffect(() => { if (fid) { setR(null); setErr(null); setPicked(null); adv.structure(c.id, fid).then(setR).catch(setErr) } }, [c.id, fid])
  const leaves = r ? flat(r.tree).filter(n => n.length > 0) : []
  return (
    <div className="space-y-4">
      <PageHeader icon={ListTree} accent="#3282b8" title="Universal File Structure Explorer"
        subtitle="The internal structure of a reconstructed file, parsed with deterministic format grammars (no LLM decides binary validity). Each component is mapped to its recovered, missing or corrupted bytes and to the evidence fragment it came from."
        action={files && <FilePicker files={files} value={fid} onChange={set} />} />
      {err != null && <ErrorBox error={err} />}
      {!r && !err && <Spinner />}
      {r && !r.available && (
        <Empty title={r.status ?? 'STRUCTURE PARSER UNAVAILABLE'}>
          {r.reason}. Registered parsers: {r.registered_parsers.join(', ')}. Nothing is guessed for unsupported formats.
        </Empty>
      )}
      {r?.available && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Tag tone="text-brand bg-tealsoft ring-teal/40">{r.parser}</Tag>
            {Object.entries(r.counts ?? {}).map(([k, v]) => v ? <Tag key={k} tone={STATE_TONE[k]}>{v} {k}</Tag> : null)}
            <span className="text-muted">{r.file_name} · {bytes(r.size)}</span>
          </div>
          <Card title="Byte layout" subtitle="Components along the file (width ∝ size). Click a block to inspect it." bodyClass="p-4">
            <div className="flex h-9 w-full overflow-hidden rounded-lg bg-track">
              {leaves.map((n, i) => (
                <motion.button key={i} initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ delay: Math.min(i * 0.01, 0.4) }}
                  onClick={() => setPicked(n)} title={`${n.name} · ${n.offset_hex} · ${bytes(n.length)} · ${n.state}`}
                  className="h-full border-r border-white/60 hover:opacity-80"
                  style={{ width: `${Math.max(0.3, (100 * n.length) / Math.max(1, r.size))}%`, background: STATE_COLOR[n.state] ?? '#8a9aa0', opacity: n.validation === 'fail' ? 0.6 : 1 }} />
              ))}
            </div>
            <div className="mt-2 flex gap-3 text-[11px] text-muted">
              {Object.entries(STATE_COLOR).map(([k, v]) => <span key={k} className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: v }} />{k}</span>)}
            </div>
          </Card>
          <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
            <Card title="Structure tree" bodyClass="p-0">
              <div className="grid grid-cols-[minmax(220px,1.6fr)_110px_90px_90px_100px_1fr] gap-2 border-b border-line bg-[#f7efe0] px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-ink2">
                <span>Component</span><span>Offset</span><span>Length</span><span>State</span><span>Confidence</span><span>Source fragment</span>
              </div>
              <div className="max-h-[560px] overflow-y-auto scroll-thin">{r.tree.map((n, i) => <TreeRow key={i} n={n} depth={0} onPick={setPicked} picked={picked} />)}</div>
            </Card>
            <Card title="Component">
              {picked ? (
                <div className="space-y-2 text-xs">
                  <div className="flex items-center gap-2"><CheckIcon status={picked.validation} /><b className="text-sm text-ink">{picked.name}</b></div>
                  <div className="flex flex-wrap gap-1.5">
                    <DataClass kind={picked.state === 'missing' ? 'missing' : picked.state === 'corrupted' ? 'corrupted' : 'observed'} />
                    <Tag tone="text-ink2 bg-track ring-line2">parser: {picked.validation}</Tag>
                  </div>
                  <div>Offset <span className="font-mono">{picked.offset_hex ?? '—'}</span> · length {bytes(picked.length)}</div>
                  <div>Confidence {picked.confidence}%</div>
                  <div>Source fragment(s): <span className="font-mono">{picked.source_fragments.join(', ') || '—'}</span></div>
                  <p className="rounded-lg bg-soft p-2 text-ink2">{picked.detail || '—'}</p>
                </div>
              ) : <p className="text-xs text-muted">Select a component in the tree or the byte layout.</p>}
              <div className="mt-3"><Hint>{r.note}</Hint></div>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
