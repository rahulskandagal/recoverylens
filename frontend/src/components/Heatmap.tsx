import { useEffect, useRef, useState } from 'react'
import type { Heatmap as H } from '../api-advanced'

export const HEAT_COLOR: Record<string, string> = {
  not_analyzed: '#d9d4c7', recovered: '#22a06b', candidate: '#ffb84d', uncertain: '#e07b39', corrupted: '#d9534f',
}
export const HEAT_LABEL: Record<string, string> = {
  recovered: 'GREEN – recovered', candidate: 'YELLOW – possible candidate', uncertain: 'ORANGE – uncertain',
  corrupted: 'RED – corrupted / unavailable', not_analyzed: 'GRAY – not analysed',
}

/** Storage heatmap: one cell per aggregated storage unit, coloured by recovery state. */
export default function Heatmap({ h, onPick, height = 150 }: { h: H; onPick?: (owner: string) => void; height?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [tip, setTip] = useState<string | null>(null)
  const byCode = Object.fromEntries(Object.entries(h.codes).map(([k, v]) => [v, k]))
  const cols = Math.max(1, Math.ceil(Math.sqrt(h.cells * 6)))
  const rows = Math.max(1, Math.ceil(h.cells / cols))
  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    const w = cv.clientWidth
    const cell = Math.max(1, w / cols)
    cv.width = w * devicePixelRatio
    cv.height = rows * cell * devicePixelRatio
    cv.style.height = `${Math.min(height, rows * cell)}px`
    const g = cv.getContext('2d')
    if (!g) return
    g.scale(devicePixelRatio, devicePixelRatio)
    h.state.forEach((s, i) => {
      g.fillStyle = HEAT_COLOR[byCode[s]] ?? '#ccc'
      g.fillRect((i % cols) * cell, Math.floor(i / cols) * cell, Math.max(1, cell - (cell > 3 ? 0.6 : 0)), Math.max(1, cell - (cell > 3 ? 0.6 : 0)))
    })
  }, [h, cols, rows, height]) // eslint-disable-line react-hooks/exhaustive-deps
  const at = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const cell = r.width / cols
    const scaleY = r.height / (rows * cell)
    const i = Math.floor((e.clientY - r.top) / (cell * scaleY)) * cols + Math.floor((e.clientX - r.left) / cell)
    return i >= 0 && i < h.cells ? i : null
  }
  return (
    <div>
      <canvas ref={ref} className="w-full cursor-crosshair rounded-lg bg-soft"
        onMouseMove={e => { const i = at(e); if (i == null) return setTip(null)
          const o = h.owner[i]; setTip(`0x${(i * h.unit).toString(16).toUpperCase().padStart(8, '0')} · ${byCode[h.state[i]].replace('_', ' ')}${o ? ` · ${h.names[o] ?? o}` : ''}`) }}
        onMouseLeave={() => setTip(null)}
        onClick={e => { const i = at(e); const o = i != null ? h.owner[i] : null; if (o && onPick) onPick(o) }} />
      <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px] text-muted">
        {Object.entries(HEAT_LABEL).map(([k, l]) => <span key={k} className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: HEAT_COLOR[k] }} />{l}</span>)}
        <span className="ml-auto font-mono">{tip ?? `${h.cells.toLocaleString()} cells × ${h.unit.toLocaleString()} B`}</span>
      </div>
    </div>
  )
}
