import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import { api, type Fragment } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, DataClass, Spinner } from '../components/ui'
import { FAMILY_COLOR, bytes } from '../lib/format'

export default function Fragments() {
  const { c } = useOutletContext<CaseCtx>()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const [family, setFamily] = useState('')
  const [assigned, setAssigned] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Awaited<ReturnType<typeof api.fragments>> | null>(null)
  const [sel, setSel] = useState<Fragment | null>(null)
  const [hex, setHex] = useState<Awaited<ReturnType<typeof api.hex>> | null>(null)
  useEffect(() => {
    api.fragments(c.id, { page, size: 60, family, assigned, q }).then(d => {
      setData(d)
      if (q && d.items.length === 1) setSel(d.items[0])
    })
  }, [c.id, page, family, assigned, q])
  useEffect(() => { if (sel) api.hex(c.id, sel.id).then(setHex); else setHex(null) }, [sel, c.id])
  if (!data) return <Spinner />
  const pages = Math.ceil(data.total / data.size)
  const sl = 'rounded-lg border border-line2 bg-soft px-2 py-1.5 text-xs text-ink'
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Fragment explorer</h1>
        <p className="text-sm text-muted">Every run of same-class clusters detected by the deterministic scanner. Bytes are read on demand from the write-protected evidence image.</p>
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative min-w-[220px] flex-1">
            <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted" />
            <input value={q} onChange={e => { setPage(1); setParams(e.target.value ? { q: e.target.value } : {}) }} placeholder="Fragment ID (F0123) or reconstruction ID (R004)…"
              className="w-full rounded-lg border border-line2 bg-soft py-1.5 pl-8 pr-3 text-sm text-ink outline-none focus:border-teal" />
          </label>
          <select value={family} onChange={e => { setPage(1); setFamily(e.target.value) }} className={sl}>
            <option value="">All families</option>{data.families.map(f => <option key={f.family} value={f.family}>{f.family} ({f.n})</option>)}
          </select>
          <select value={assigned} onChange={e => { setPage(1); setAssigned(e.target.value) }} className={sl}>
            <option value="">Assigned & unassigned</option><option value="yes">Used by a reconstruction</option><option value="no">Unassigned</option>
          </select>
          <span className="ml-auto text-xs text-muted">{data.total.toLocaleString()} fragments</span>
        </div>
      </Card>
      <div className="grid gap-4 xl:grid-cols-[1fr_520px]">
        <Card className="overflow-hidden">
          <div className="scroll-thin -m-4 max-h-[640px] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-soft text-left text-[10px] uppercase tracking-wider text-muted">
                <tr><th className="py-2 pl-4">ID</th><th>Offset</th><th>Length</th><th>Family</th><th>Entropy</th><th>Signals</th><th className="pr-4">Assigned</th></tr>
              </thead>
              <tbody className="font-mono">
                {data.items.map(f => (
                  <tr key={f.id} onClick={() => setSel(f)} className={`cursor-pointer border-t border-line ${sel?.id === f.id ? 'bg-teal/10' : 'hover:bg-soft'}`}>
                    <td className="py-1.5 pl-4 text-ink">{f.id}</td><td className="text-muted">{f.offset_hex}</td><td className="text-muted">{bytes(f.length)}</td>
                    <td><span className="inline-flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: FAMILY_COLOR[f.family] ?? '#64748b' }} />{f.family}</span></td>
                    <td className="text-muted">{f.entropy.toFixed(2)}</td>
                    <td className="space-x-1 font-sans text-[10px]">
                      {f.header && <span className="rounded bg-teal/15 px-1 text-brand">header:{String(f.header.type)}</span>}
                      {f.has_footer && <span className="rounded bg-emerald-500/15 px-1 text-success">footer</span>}
                      {f.rst_markers > 0 && <span className="rounded bg-track px-1 text-ink2">RST×{f.rst_markers}</span>}
                      {f.pdf_objects.length > 0 && <span className="rounded bg-track px-1 text-ink2">obj {f.pdf_objects[0]}–{f.pdf_objects[f.pdf_objects.length - 1]}</span>}
                      {f.zip_members.length > 0 && <span className="rounded bg-track px-1 text-ink2">{f.zip_members.length} zip hdr</span>}
                      {f.duplicate_of && <span className="rounded bg-pink-500/15 px-1 text-pink-700">dup of {f.duplicate_of}</span>}
                    </td>
                    <td className="pr-4">{f.assigned_to ? <Link to={`../files/${f.assigned_to}`} onClick={e => e.stopPropagation()} className="text-brand hover:underline">{f.assigned_to}</Link> : <span className="text-muted/70">—</span>}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
          {pages > 1 && <div className="-mx-4 -mb-4 mt-4 flex items-center justify-center gap-2 border-t border-line py-2 text-xs">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded px-2 py-1 text-ink2 disabled:opacity-30">‹ Prev</button>
            <span className="text-muted">Page {page} / {pages}</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded px-2 py-1 text-ink2 disabled:opacity-30">Next ›</button></div>}
        </Card>
        <Card title={sel ? `Fragment ${sel.id}` : 'Fragment inspector'} action={sel && <DataClass kind="observed" />} className="self-start">
          {!sel ? <p className="text-sm text-muted">Select a fragment to see its metadata and raw bytes.</p> : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 font-mono text-[11px] text-ink2">
                <div>offset {sel.offset_hex}</div><div>sector {sel.sector.toLocaleString()}</div>
                <div>clusters {sel.start_cluster}–{sel.start_cluster + sel.clusters - 1}</div><div>{bytes(sel.length)}</div>
                <div>classes: {sel.classes.join(', ')}</div><div>entropy {sel.entropy}</div>
                {sel.ts_first && <div className="col-span-2">timestamps {sel.ts_first} → {sel.ts_last}</div>}
                <div className="col-span-2 break-all text-muted">sha256 {sel.sha256}</div>
              </div>
              {sel.header && <pre className="scroll-thin max-h-32 overflow-auto rounded bg-soft p-2 font-mono text-[10px] text-muted">{JSON.stringify(sel.header, null, 1)}</pre>}
              <div className="rounded-md bg-canvas p-2 font-mono text-[10.5px] leading-[1.45]">
                {!hex ? <Spinner /> : hex.lines.map(l => (
                  <div key={l.offset} className="flex gap-3 whitespace-pre"><span className="text-muted/70">{l.offset}</span><span className="text-ink2">{l.hex.padEnd(47)}</span><span className="text-success">{l.ascii}</span></div>))}
              </div>
              <p className="text-[10px] text-muted">First 512 bytes, read-only from the evidence image.</p>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
