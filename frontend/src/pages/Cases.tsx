import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type CaseRow } from '../api'
import { Card, ErrorBox, Spinner } from '../components/ui'
import { bytes } from '../lib/format'

export default function Cases() {
  const [rows, setRows] = useState<CaseRow[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const load = () => api.cases().then(setRows).catch(setErr)
  useEffect(() => { load() }, [])
  const del = async (id: string) => {
    if (!confirm('Remove this case and its analysis results? The evidence copy is kept on disk.')) return
    await api.deleteCase(id)
    load()
  }
  return (
    <div className="mx-auto max-w-[1200px] space-y-4 px-4 py-6">
      <h1 className="text-2xl font-semibold text-ink">Cases</h1>
      {err != null && <ErrorBox error={err} />}
      <Card>
        {!rows ? <Spinner /> : rows.length === 0 ? (
          <p className="p-4 text-sm text-muted">No cases yet. <Link to="/new" className="text-brand">Start an analysis</Link>.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-wider text-muted">
              <tr><th className="py-2">Case</th><th>Image</th><th>Mode</th><th>Status</th><th>Created</th><th /></tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-t border-line">
                  <td className="py-2.5"><Link to={r.status === 'complete' ? `/cases/${r.id}` : `/cases/${r.id}/progress`} className="font-medium text-brand hover:underline">{r.name}</Link>
                    <div className="font-mono text-[11px] text-muted">{r.id}</div></td>
                  <td className="font-mono text-xs text-muted">{r.image_name}<div className="text-muted/70">{bytes(r.image_size)}</div></td>
                  <td><span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${r.mode === 'demo' ? 'bg-amber-500/15 text-amber-800' : 'bg-teal/15 text-brand'}`}>
                    {r.mode === 'demo' ? 'synthetic' : r.mode === 'recyclebin' ? 'recycle bin' : 'upload'}</span></td>
                  <td className={r.status === 'complete' ? 'text-success' : r.status === 'failed' ? 'text-danger' : 'text-amber-800'}>{r.status}</td>
                  <td className="text-xs text-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="text-right"><button onClick={() => del(r.id)} className="rounded p-1.5 text-muted hover:bg-tealsoft hover:text-danger" title="Remove case"><Trash2 className="h-4 w-4" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
