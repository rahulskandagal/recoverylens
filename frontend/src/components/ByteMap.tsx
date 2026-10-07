import { useState } from 'react'
import type { SegmentT } from '../api'
import { bytes } from '../lib/format'

const KIND = {
  recovered: { cls: 'bg-emerald-500/80', label: 'Recovered (observed bytes)' },
  corrupted: { cls: 'bg-amber-400', label: 'Present but fails validation' },
  missing: { cls: 'hatch bg-red-50', label: 'Not located (zero-filled in export)' },
}

export default function ByteMap({ segments, total }: { segments: SegmentT[]; total: number }) {
  const [h, setH] = useState<SegmentT | null>(null)
  const T = Math.max(total, segments.reduce((m, s) => Math.max(m, s.end), 0), 1)
  return (
    <div>
      <div className="relative flex h-9 w-full overflow-hidden rounded-md ring-1 ring-line2" onMouseLeave={() => setH(null)}>
        {segments.map((s, i) => (
          <div key={i} onMouseEnter={() => setH(s)}
            className={`h-full ${KIND[s.kind].cls} ${i ? 'border-l border-card' : ''} ${h === s ? 'brightness-125' : ''}`}
            style={{ width: `${(s.length / T) * 100}%`, minWidth: s.kind !== 'recovered' ? 3 : 1 }} />
        ))}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-muted/70"><span>0</span><span>{bytes(T)}</span></div>
      <div className="mt-2 min-h-[38px] rounded-md bg-soft px-3 py-2 font-mono text-[11px] text-ink2">
        {h ? <>
          <span className="font-sans font-semibold">{KIND[h.kind].label}</span> · logical {h.start.toLocaleString()}–{h.end.toLocaleString()} ({bytes(h.length)})
          {h.fragment_id && <> · from <span className="text-brand">{h.fragment_id}</span> @ {h.source_offset_hex}</>}
          {h.note && <div className="mt-0.5 font-sans text-muted">{h.note}</div>}
        </> : <span className="font-sans text-muted">Hover a segment to see where its bytes came from.</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-muted">
        {Object.values(KIND).map(k => <span key={k.label} className="flex items-center gap-1.5"><span className={`h-3 w-5 rounded-sm ${k.cls}`} />{k.label}</span>)}
      </div>
    </div>
  )
}
