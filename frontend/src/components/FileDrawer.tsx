import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type FileDetail } from '../api'
import ExportPanel from './ExportPanel'
import FilePreview from './FilePreview'
import { CheckIcon, ErrorBox, Spinner, StatusBadge } from './ui'

/** Side panel: 'integrity' shows the validator checklist, 'preview' the recovered file. */
export default function FileDrawer({ caseId, fileId, mode, onClose }: {
  caseId: string; fileId: string; mode: 'integrity' | 'preview' | 'export'; onClose: () => void
}) {
  const [f, setF] = useState<FileDetail | null>(null)
  const [err, setErr] = useState<unknown>(null)
  useEffect(() => { setF(null); api.file(caseId, fileId).then(setF).catch(setErr) }, [caseId, fileId])
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const checks = f?.validator_checks?.length ? f.validator_checks : null
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={onClose} role="dialog" aria-modal="true"
      aria-label={mode === 'integrity' ? 'Integrity checklist' : mode === 'export' ? 'Export' : 'File preview'}>
      <aside className="scroll-thin h-full w-full max-w-xl overflow-y-auto border-l border-line2 bg-card p-5 shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-muted">{mode === 'integrity' ? 'Integrity checklist' : mode === 'export' ? 'Export & validation' : 'Preview'}</div>
            <div className="truncate font-semibold text-ink">{f?.file_name ?? fileId}</div>
          </div>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-tealsoft hover:text-brand" aria-label="Close"><X className="h-5 w-5" /></button>
        </div>
        {err != null && <ErrorBox error={err} />}
        {!f && !err && <Spinner />}
        {f && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2"><StatusBadge status={f.recovery_status} size="md" />
              <span className="font-mono text-sm text-ink2">integrity {f.integrity_score.toFixed(1)}/100</span></div>
            <p className="rounded-md bg-soft p-3 text-xs leading-relaxed text-ink2"><b className="text-ink">Why this status: </b>{f.status_reason}</p>
            {mode === 'integrity' ? (
              <>
                {checks ? (
                  <table className="w-full text-xs">
                    <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
                      <tr><th className="py-1">Check</th><th className="text-right">Weight</th><th className="text-right">Points</th></tr>
                    </thead>
                    <tbody>
                      {checks.map((x, i) => (
                        <tr key={i} className="border-t border-line align-top">
                          <td className="py-1.5 pr-2">
                            <div className="flex gap-1.5"><CheckIcon status={x.status} /><span className="text-ink">{x.name}</span></div>
                            <div className="ml-5.5 mt-0.5 text-[11px] text-muted">{x.detail}</div>
                          </td>
                          <td className="py-1.5 text-right font-mono text-muted">{x.weight ?? '—'}</td>
                          <td className="py-1.5 text-right font-mono text-ink">{x.status === 'na' ? 'n/a' : x.points != null ? x.points.toFixed(1) : x.status}</td>
                        </tr>))}
                    </tbody>
                  </table>
                ) : (
                  <table className="w-full text-xs"><tbody>
                    {f.integrity_factors.map(x => (
                      <tr key={x.factor} className="border-t border-line align-top">
                        <td className="py-1.5 pr-2"><div className="text-ink">{x.factor}</div><div className="text-[11px] text-muted">{x.detail}</div></td>
                        <td className="py-1.5 text-right font-mono text-ink">{x.points >= 0 ? '+' : ''}{x.points}{x.max ? `/${x.max}` : ''}</td>
                      </tr>))}
                  </tbody></table>
                )}
                <p className="text-[11px] text-muted">
                  {checks?.some(x => x.weight) ? 'Integrity = weighted mean of the scored checks (n/a checks excluded). Partial checks score their pass ratio.'
                    : 'Integrity = sum of the listed factors, clamped to 0–100.'}
                </p>
                {f.repaired_copy && (
                  <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-800">
                    <b>Repaired copy</b> ({f.repaired_copy.method}): integrity {f.repaired_copy.integrity_score.toFixed(1)}, {f.repaired_copy.render_clean}/{f.repaired_copy.render_pages} pages render.
                    {f.repaired_copy.synthesized_objects.length > 0 && <> Synthesized structure: {f.repaired_copy.synthesized_objects.join('; ')}.</>}
                    <div className="mt-1 font-mono text-[10px] text-amber-800">carved {f.repaired_copy.carved_sha256.slice(0, 16)}… → repaired {f.repaired_copy.sha256.slice(0, 16)}…</div>
                  </div>
                )}
              </>
            ) : (
              <>
                <ExportPanel f={f} caseId={caseId} />
                {mode === 'preview' && <FilePreview f={f} caseId={caseId} maxHeight={560} />}
              </>
            )}
            <Link to={`/cases/${caseId}/files/${f.file_id}`} className="block text-xs text-brand hover:underline" onClick={onClose}>Open full file analysis →</Link>
          </div>
        )}
      </aside>
    </div>
  )
}
