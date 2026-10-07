import { useEffect, useState } from 'react'
import { Copy, Download, FileDown } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, Spinner } from '../components/ui'

function JsonNode({ k, v, depth }: { k?: string; v: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 2)
  const isObj = v !== null && typeof v === 'object'
  if (!isObj) {
    const cls = typeof v === 'string' ? 'text-success' : typeof v === 'number' ? 'text-brand' : 'text-purple'
    return <div className="pl-4">{k != null && <span className="text-muted">"{k}": </span>}<span className={cls}>{JSON.stringify(v)}</span></div>
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as object)
  return (
    <div className="pl-4">
      <button onClick={() => setOpen(!open)} className="text-left hover:text-brand">
        <span className="inline-block w-3 text-muted">{open ? '▾' : '▸'}</span>
        {k != null && <span className="text-muted">"{k}": </span>}
        <span className="text-muted">{Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && entries.map(([ck, cv]) => <JsonNode key={ck} k={Array.isArray(v) ? undefined : ck} v={cv} depth={depth + 1} />)}
    </div>
  )
}

export default function Report() {
  const { c } = useOutletContext<CaseCtx>()
  const [rep, setRep] = useState<Record<string, unknown> | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => { api.report(c.id).then(setRep) }, [c.id])
  if (!rep) return <Spinner />
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">Reports</h1>
          <p className="text-sm text-muted">A printable forensic PDF and the machine-readable JSON report. Both are rendered from the same analysis records, so they always agree.</p>
        </div>
        <div className="flex gap-2">
          <a href={`/api/cases/${c.id}/report.pdf`} className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-brand-deep hover:bg-accent-2"><FileDown className="h-4 w-4" />Forensic PDF report</a>
          <button onClick={() => { navigator.clipboard.writeText(JSON.stringify(rep, null, 2)); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-line2 px-3 py-1.5 text-sm text-ink hover:bg-tealsoft"><Copy className="h-4 w-4" />{copied ? 'Copied' : 'Copy'}</button>
          <a href={`/api/cases/${c.id}/report?download=true`} className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-deep"><Download className="h-4 w-4" />JSON report</a>
        </div>
      </div>
      <Card>
        <div className="scroll-thin -ml-4 max-h-[75vh] overflow-auto font-mono text-xs leading-relaxed text-ink2">
          <JsonNode v={rep} depth={0} />
        </div>
      </Card>
    </div>
  )
}
