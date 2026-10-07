import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { DiskMap as DM, FileRow } from '../api'
import { STATUS_META, bytes, plural } from '../lib/format'

const CLS_NAMES: Record<number, string> = { 0: 'empty (zero-filled)', 1: 'high-entropy (unidentified)', 2: 'plain text', 3: 'log text', 4: 'PDF data', 5: 'JPEG header', 6: 'JPEG scan data', 7: 'SQLite page', 8: 'ZIP structure', 9: 'XML', 10: 'FAT directory', 11: 'binary' }
const UNASSIGNED = '#c7d3d6'
const EMPTY = '#f3ecdd'
// per-file colours: fixed categorical order (identity follows the file, never its rank)
const FILE_COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948']

export default function DiskMap({ map, files, caseId }: { map: DM; files: FileRow[]; caseId: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const nav = useNavigate()
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null)
  const [width, setWidth] = useState(900)
  const byId = useMemo(() => Object.fromEntries(files.map(f => [f.file_id, f])), [files])
  const unit = map.unit ?? 4096
  const n = map.clusters
  // scale cells to the input: a 4-sector file gets large cells, a 3,600-cluster image small ones
  const cell = Math.max(4, Math.min(56, Math.floor(Math.sqrt((width * 160) / Math.max(1, n)))))
  const cols = Math.max(1, Math.min(n, Math.floor(width / cell)))
  const rows = Math.ceil(n / cols)
  const colorOf = (fid: string) => FILE_COLORS[map.files.indexOf(fid) % FILE_COLORS.length]
  useEffect(() => {
    const ro = new ResizeObserver(e => setWidth(e[0].contentRect.width))
    if (wrap.current) ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    const cv = ref.current
    const g = cv?.getContext('2d')
    if (!cv || !g) return
    const dpr = window.devicePixelRatio || 1
    cv.width = cols * cell * dpr
    cv.height = rows * cell * dpr
    cv.style.width = `${cols * cell}px`
    cv.style.height = `${rows * cell}px`
    g.scale(dpr, dpr)
    const gap = cell >= 12 ? 2 : 1
    for (let i = 0; i < n; i++) {
      const x = (i % cols) * cell, y = Math.floor(i / cols) * cell
      const o = map.owner[i]
      g.fillStyle = o >= 0 ? colorOf(map.files[o]) : map.cls[i] === 0 ? EMPTY : UNASSIGNED
      g.fillRect(x, y, cell - gap, cell - gap)
      if (map.damaged?.[i]) {  // hatched = present but fails validation
        g.save()
        g.beginPath(); g.rect(x, y, cell - gap, cell - gap); g.clip()
        g.strokeStyle = 'rgba(23,50,58,0.55)'; g.lineWidth = Math.max(1, cell / 14)
        for (let d = -cell; d < cell; d += Math.max(3, cell / 5)) { g.beginPath(); g.moveTo(x + d, y + cell); g.lineTo(x + d + cell, y); g.stroke() }
        g.restore()
      }
    }
  }, [map, cols, rows, cell, n]) // eslint-disable-line react-hooks/exhaustive-deps
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current!.getBoundingClientRect()
    const cx = Math.floor((e.clientX - r.left) / cell), cy = Math.floor((e.clientY - r.top) / cell)
    const i = cy * cols + cx
    setHover(i >= 0 && i < n && cx < cols ? { i, x: e.clientX - r.left, y: e.clientY - r.top } : null)
  }
  const hf = hover && map.owner[hover.i] >= 0 ? byId[map.files[map.owner[hover.i]]] : undefined
  const hex = (v: number) => '0x' + v.toString(16).toUpperCase().padStart(8, '0')
  const used = map.owner.filter(o => o >= 0).length
  const legendFiles = map.files.filter(fid => map.owner.some(o => o >= 0 && map.files[o] === fid))
  return (
    <div ref={wrap} className="relative">
      <div className="mb-2 text-[11px] text-muted">
        {plural(n, unit === 512 ? 'sector' : 'cluster')} of {bytes(unit)} · {bytes(map.size ?? n * unit)} scanned · {plural(used, 'cell')} used by recovered files
      </div>
      <canvas ref={ref} onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        onClick={() => hf && nav(`/cases/${caseId}/files/${hf.file_id}`)}
        className={hf ? 'cursor-pointer' : 'cursor-crosshair'} role="img"
        aria-label={`Storage map: ${n} cells of ${unit} bytes, coloured by the recovered file that uses them`} />
      {hover && (
        <div className="pointer-events-none absolute z-10 w-72 rounded-lg border border-line2 bg-soft p-2.5 text-xs shadow-xl"
          style={{ left: Math.max(0, Math.min(hover.x + 14, width - 290)), top: hover.y + 34 }}>
          <div className="font-mono text-ink2">bytes {hex(hover.i * unit)} – {hex(Math.min((map.size ?? Infinity), (hover.i + 1) * unit) - 1)}</div>
          <div className="font-mono text-[10px] text-muted">{unit === 512 ? 'sector' : 'cluster'} {hover.i} · {(hover.i * unit).toLocaleString()}–{(Math.min(map.size ?? Infinity, (hover.i + 1) * unit) - 1).toLocaleString()}</div>
          <div className="mt-1 text-ink">{CLS_NAMES[map.cls[hover.i]]}</div>
          {map.damaged?.[hover.i] ? <div className="text-amber-800">contains bytes that fail validation</div> : null}
          {hf ? <div className="mt-1.5 border-t border-line pt-1.5"><div className="font-medium text-ink">{hf.file_name}</div>
            <div className="text-muted">{STATUS_META[hf.recovery_status].label} · click to open</div></div>
            : <div className="mt-1 text-muted">{map.cls[hover.i] === 0 ? 'Empty' : 'Not used by any recovered file'}</div>}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-muted">
        {legendFiles.slice(0, 8).map(fid => (
          <span key={fid} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: colorOf(fid) }} />{map.file_names?.[fid] ?? byId[fid]?.file_name ?? fid}</span>))}
        {legendFiles.length > 8 && <span>+{legendFiles.length - 8} more files</span>}
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: UNASSIGNED }} />unassigned data</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm ring-1 ring-line2" style={{ background: EMPTY }} />empty</span>
        {map.damaged?.some(Boolean) && <span className="flex items-center gap-1.5"><span className="hatch h-2.5 w-2.5 rounded-sm bg-slate-500" />fails validation</span>}
      </div>
    </div>
  )
}
