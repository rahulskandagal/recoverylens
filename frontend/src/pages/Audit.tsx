import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { api } from '../api'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, Spinner } from '../components/ui'

const STAGE_COLOR: Record<string, string> = {
  ingest: 'text-brand', scan: 'text-cyan-700', identify: 'text-teal-700', relate: 'text-purple', reconstruct: 'text-indigo-700',
  validate: 'text-success', classify: 'text-lime-700', prioritize: 'text-amber-800', verify: 'text-success', report: 'text-brand',
  evaluate: 'text-pink-700', error: 'text-danger',
}

export default function Audit() {
  const { c } = useOutletContext<CaseCtx>()
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.audit>> | null>(null)
  useEffect(() => { api.audit(c.id).then(setRows) }, [c.id])
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Audit trail</h1>
        <p className="text-sm text-muted">Chronological record of every analysis operation, including evidence hashing before and after analysis and every priority change.</p>
      </div>
      <Card>
        {!rows ? <Spinner /> : (
          <ol className="space-y-1 font-mono text-xs">
            {rows.map(r => (
              <li key={r.id} className="grid grid-cols-[150px_90px_1fr] gap-3 border-b border-line py-1">
                <span className="text-muted">{r.ts.replace('T', ' ').replace('+00:00', 'Z')}</span>
                <span className={STAGE_COLOR[r.stage] ?? 'text-ink2'}>{r.stage}</span>
                <span className="text-ink2">{r.message}</span>
              </li>))}
          </ol>)}
      </Card>
    </div>
  )
}
