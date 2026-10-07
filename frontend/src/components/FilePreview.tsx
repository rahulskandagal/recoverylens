import { useEffect, useState } from 'react'
import { AlertTriangle, Fingerprint, ShieldX } from 'lucide-react'
import type { FileDetail } from '../api'
import { sha256Hex } from '../lib/format'
import { DataClass, Spinner } from './ui'

function Unavailable({ reason }: { reason: string }) {
  return (
    <div className="flex gap-3 rounded-xl border border-dashed border-line2 bg-soft p-4 text-sm text-ink2">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-accent" />
      <div><div className="font-bold uppercase tracking-wide text-ink">Preview unavailable</div><div className="mt-0.5 text-xs">Reason: {reason}</div></div>
    </div>
  )
}

/** Renders a PDF from a Blob of the SAME bytes that the export serves (hash-checked). */
function PdfBlobViewer({ f, caseId }: { f: FileDetail; caseId: string }) {
  const [url, setUrl] = useState<string | null>(null)
  const [sha, setSha] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let u: string | null = null
    let alive = true
    fetch(`/api/cases/${caseId}/files/${f.file_id}/download?inline=true`)
      .then(async r => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.reason ?? `${r.status} ${r.statusText}`)
        return r.arrayBuffer()
      })
      .then(async buf => {
        const h = await sha256Hex(buf)
        if (!alive) return
        u = URL.createObjectURL(new Blob([buf], { type: 'application/pdf' }))
        setSha(h); setUrl(u)
      })
      .catch(e => alive && setErr(String(e.message ?? e)))
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [caseId, f.file_id])
  if (err) return <Unavailable reason={err} />
  if (!url) return <Spinner label="Loading reconstructed bytes…" />
  const same = sha === f.export?.reconstruction_sha256
  return (
    <div className="space-y-2">
      <div className={`flex items-center gap-1.5 text-[11px] ${same ? 'text-success' : 'text-danger'}`}>
        <Fingerprint className="h-3.5 w-3.5" />
        {same ? 'Rendered from the exact export bytes (SHA-256 matches the reconstruction)' : 'Warning: preview bytes do not match the reconstruction hash'}
      </div>
      <object data={url} type="application/pdf" className="h-[480px] w-full rounded-xl border border-line bg-white" aria-label={`PDF preview of ${f.file_name}`}>
        <Unavailable reason="this browser cannot display PDFs inline; use Export to open the file" />
      </object>
    </div>
  )
}

export default function FilePreview({ f, caseId, maxHeight = 440 }: { f: FileDetail; caseId: string; maxHeight?: number }) {
  const p = f.preview
  const [imgErr, setImgErr] = useState(false)
  if (f.security?.status === 'MALWARE_DETECTED') return (
    <div className="flex gap-3 rounded-xl border-2 border-danger bg-red-50 p-4 text-sm text-red-900">
      <ShieldX className="h-5 w-5 shrink-0 text-danger" />
      <div><div className="font-bold uppercase">Preview unavailable</div><div className="text-xs">Reason: the artifact is flagged as malware, so it is never rendered, opened or executed.</div></div>
    </div>
  )
  if (f.error) return <Unavailable reason={f.error} />
  if (!p) return <Unavailable reason="the content cannot be rendered from the recovered bytes alone" />
  if (p.kind === 'image') return imgErr ? <Unavailable reason="the reconstructed image bytes could not be decoded for display" /> : (
    <div>
      <div className="mb-2 flex items-center gap-2">{p.unverified ? <DataClass kind="unverified" /> : <DataClass kind="reconstructed" />}<span className="text-[11px] text-muted">{p.note}</span></div>
      <img src={`/api/cases/${caseId}/files/${f.file_id}/preview`} onError={() => setImgErr(true)} alt={`Preview of ${f.file_name}`}
        className="w-full rounded-xl object-contain ring-1 ring-line2" style={{ maxHeight }} />
    </div>
  )
  if (p.kind === 'pdf') {
    const broken = p.inventory.filter(o => !o.intact)
    const renderable = f.export && f.export.validation_state !== 'failed'
    return (
      <div className="space-y-3">
        {renderable ? <PdfBlobViewer f={f} caseId={caseId} />
          : <Unavailable reason={`reconstructed bytes failed PDF validation (${f.export?.validation_reason ?? 'see validation checks'})`} />}
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <DataClass kind="reconstructed" />
          <span className={p.render_clean === p.render_pages && p.render_pages > 0 ? 'text-success' : 'text-amber-800'}>
            {p.render_pages ? `${p.render_clean}/${p.render_pages} page(s) rendered cleanly` : 'No page could be rendered'}
          </span>
          {p.renderer && <span className="text-muted">· {p.renderer}{p.repaired_by_renderer ? ' (had to repair the structure)' : ''}</span>}
        </div>
        {p.images.length > 0 ? (
          <div className="scroll-thin flex gap-3 overflow-x-auto pb-1" style={{ maxHeight }}>
            {p.images.map(im => (
              <figure key={im.page} className="shrink-0">
                <img src={`/api/cases/${caseId}/files/${f.file_id}/preview?page=${im.page}`} alt={`Page ${im.page} of ${f.file_name}`}
                  className="h-[300px] rounded bg-white object-contain ring-1 ring-line2" />
                <figcaption className="mt-1 text-center text-[10px] text-muted">page {im.page}</figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-line2 bg-soft p-3 text-xs text-muted">
            No page image: the page tree or page content is damaged. The surviving object inventory is shown instead, and nothing was invented to fill the gaps.
          </div>
        )}
        {p.inventory.length > 0 && (
          <details open={p.images.length === 0} className="rounded-md bg-soft p-2 text-xs">
            <summary className="cursor-pointer text-ink2">Object inventory: {p.inventory.length - broken.length}/{p.inventory.length} intact{broken.length ? `, ${broken.length} damaged` : ''}</summary>
            <table className="mt-2 w-full font-mono text-[11px]">
              <thead className="text-left text-[10px] uppercase text-muted"><tr><th>obj</th><th>type</th><th>bytes</th><th>state</th></tr></thead>
              <tbody>{p.inventory.slice(0, 60).map(o => (
                <tr key={o.num} className="border-t border-line">
                  <td className="py-0.5">{o.num}</td><td>{o.type || '—'}</td><td>{o.offset}–{o.end}</td>
                  <td className={o.intact ? 'text-success' : 'text-danger'}>{o.intact ? 'intact' : o.reason}</td>
                </tr>))}</tbody>
            </table>
            {p.damaged_ranges.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-[11px] text-amber-800">{p.damaged_ranges.map(([a, b, n], i) => <li key={i}>bytes {a}–{b}: {n}</li>)}</ul>
            )}
          </details>
        )}
        {p.pages.length > 0 && (
          <details className="rounded-md bg-soft p-2 text-xs">
            <summary className="cursor-pointer text-ink2">Extracted text ({p.pages.length} page(s))</summary>
            {p.pages.map(pg => <pre key={pg.page} className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-muted">{`— page ${pg.page} —\n`}{pg.text.slice(0, 900)}</pre>)}
          </details>
        )}
      </div>
    )
  }
  if (p.kind === 'pages') return (
    <div className="scroll-thin space-y-3 overflow-y-auto" style={{ maxHeight }}>
      {p.pages.map(pg => (
        <div key={pg.page} className="rounded-md bg-soft p-3">
          <div className="mb-1 text-[11px] font-semibold text-ink2">Page {pg.page}</div>
          <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-muted">{pg.text.slice(0, 900)}</pre>
        </div>))}
    </div>
  )
  if (p.kind === 'text') return (
    <div>
      {p.verified === false && <div className="mb-2 flex items-center gap-2"><DataClass kind="unverified" /><span className="text-[11px] text-muted">No checksum available for this text</span></div>}
      {p.verified === true && <div className="mb-2 flex items-center gap-2"><DataClass kind="reconstructed" /><span className="text-[11px] text-success">Extracted from a CRC-32 verified member</span></div>}
      <pre className="scroll-thin overflow-auto whitespace-pre-wrap rounded-md bg-soft p-3 font-mono text-[11px] leading-relaxed text-ink2" style={{ maxHeight }}>{p.text}</pre>
    </div>
  )
  return (
    <div className="scroll-thin space-y-4 overflow-auto" style={{ maxHeight }}>
      {p.tables.map(t => (
        <div key={t.name}>
          <div className="mb-1 flex flex-wrap items-baseline gap-2 text-xs">
            <span className="font-semibold text-ink">{t.name}</span>
            <span className="font-mono text-success">{t.rows_recovered.toLocaleString()}</span><span className="text-muted">rows recovered of ~{t.rows_expected?.toLocaleString() ?? '?'} expected</span>
          </div>
          <table className="w-full text-[11px]">
            <thead><tr>{t.columns.map(col => <th key={col} className="border-b border-line px-1.5 py-1 text-left font-medium text-muted">{col}</th>)}</tr></thead>
            <tbody className="font-mono">{t.sample.slice(0, 12).map((r, i) => <tr key={i}>{t.columns.map(col => <td key={col} className="border-b border-line px-1.5 py-0.5 text-ink2">{String(r[col] ?? '')}</td>)}</tr>)}</tbody>
          </table>
        </div>))}
    </div>
  )
}
