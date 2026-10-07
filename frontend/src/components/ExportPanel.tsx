import { useEffect, useState } from 'react'
import { AlertTriangle, BadgeCheck, CheckCircle2, Download, FileArchive, Fingerprint, FolderOutput, Loader2, ShieldX, Wrench } from 'lucide-react'
import { api, type FileDetail } from '../api'
import { realApi, type RestoreResult } from '../api-real'
import { sha256Hex } from '../lib/format'
import { ErrorBox } from './ui'

type Result = { variant: string; name: string; size: number; clientSha: string; expected: string | null; identical: boolean }

const CLASS_STYLE = {
  validated_file: { box: 'border-emerald-300 bg-emerald-50 text-emerald-900', icon: BadgeCheck, iconCls: 'text-success' },
  partially_validated_file: { box: 'border-amber-300 bg-amber-50 text-amber-950', icon: AlertTriangle, iconCls: 'text-accent' },
  forensic_artifact: { box: 'border-red-200 bg-red-50 text-red-900', icon: AlertTriangle, iconCls: 'text-danger' },
}

/**
 * Export buttons are plain links to the download endpoint, so the browser saves the server's
 * bytes directly (the server audits the served SHA-256). A programmatic click on a Blob URL made
 * after async work is no longer a user gesture and browsers may silently drop that download.
 * Verification is a separate action: bytes are fetched as an ArrayBuffer, re-hashed in the
 * browser (WebCrypto SHA-256) and the hash is reported to the audit trail. Nothing is saved.
 */
export default function ExportPanel({ f, caseId }: { f: FileDetail; caseId: string }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [res, setRes] = useState<Result | null>(null)
  const [authorized, setAuthorized] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [customDest, setCustomDest] = useState('')
  const [useCustomDest, setUseCustomDest] = useState(false)
  const [restoreResult, setRestoreResult] = useState<RestoreResult | null>(null)
  // Defaults to hidden until /api/config resolves, so "Restore to folder" never appears then
  // vanishes among the other buttons right as the page loads.
  const [publicDemo, setPublicDemo] = useState(true)
  useEffect(() => { api.config().then(c => setPublicDemo(c.public_demo)).catch(() => setPublicDemo(false)) }, [])
  const e = f.export
  const sec = f.security
  const malware = sec?.status === 'MALWARE_DETECTED'
  if (!e) return <ErrorBox title="Export failed" error={f.error ?? 'no export artifact was produced for this candidate'} />
  const st = CLASS_STYLE[e.class]

  const href = (variant: string) =>
    `/api/cases/${encodeURIComponent(caseId)}/files/${encodeURIComponent(f.file_id)}/download?variant=${variant}${malware && authorized ? '&authorized=true' : ''}`

  const verify = async (variant: string) => {
    setBusy(variant); setErr(null); setRes(null)
    try {
      if (!crypto?.subtle) throw new Error('WebCrypto is unavailable (open the app via http://localhost or HTTPS to verify hashes)')
      const out = await api.exportBytes(caseId, f.file_id, variant, malware && authorized)
      const clientSha = await sha256Hex(out.buf)
      const v = await api.verifyExport(caseId, f.file_id, variant, clientSha, out.buf.byteLength)
      setRes({ variant, name: out.name, size: out.buf.byteLength, clientSha, expected: out.expectedSha, identical: v.byte_identical })
    } catch (x) { setErr(x) }
    setBusy(null)
  }

  const doRestore = async () => {
    setRestoring(true); setErr(null); setRestoreResult(null)
    try { setRestoreResult(await realApi.restore(caseId, f.file_id, useCustomDest && customDest.trim() ? customDest.trim() : null, !malware || authorized)) }
    catch (x) { setErr(x) } finally { setRestoring(false) }
  }

  const primaryLabel = e.class === 'forensic_artifact' ? 'Export forensic byte artifact' : 'Export reconstructed file'
  const primaryDisabled = !e.exported || (malware && !authorized)
  const linkCls = (base: string, disabled = false) =>
    `inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-sm ${base} ${disabled ? 'pointer-events-none cursor-not-allowed opacity-40' : ''}`
  return (
    <div className="space-y-3">
      <div className={`flex gap-3 rounded-xl border p-3 text-sm ${st.box}`}>
        <st.icon className={`mt-0.5 h-5 w-5 shrink-0 ${st.iconCls}`} />
        <div>
          <div className="font-bold tracking-wide">{e.label}</div>
          <div className="text-xs opacity-90">{e.warning ?? 'Every format check passed; the export is the exact reconstructed byte buffer.'}</div>
          <div className="mt-1 text-[11px] opacity-80">Validation: {e.validation_reason}</div>
        </div>
      </div>
      {malware && (
        <div className="rounded-xl border-2 border-danger bg-red-50 p-3 text-sm text-red-900">
          <div className="flex items-center gap-2 font-bold"><ShieldX className="h-5 w-5 text-danger" />MALWARE DETECTED{sec?.is_test ? ' (SIMULATED / TEST SIGNATURE)' : ''}</div>
          <p className="mt-1 text-xs">Malicious content detected. The recovered artifact has NOT been executed. Normal export and inline preview are disabled.
            A forensic export is saved as <span className="font-mono">.QUARANTINED</span> with a generic binary type so it will not open automatically.</p>
          <label className="mt-2 flex items-start gap-2 text-xs font-medium">
            <input type="checkbox" checked={authorized} onChange={x => setAuthorized(x.target.checked)} className="mt-0.5 accent-[#d9534f]" />
            I am authorised to handle this artifact as evidence and will not execute it.
          </label>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <a href={primaryDisabled ? undefined : href('export')} download aria-disabled={primaryDisabled}
          className={linkCls(`font-semibold shadow ${e.class === 'forensic_artifact' || malware ? 'bg-danger text-white hover:brightness-110' : 'bg-brand text-white hover:bg-brand-deep'}`, primaryDisabled)}>
          <Download className="h-4 w-4" />
          {malware ? 'Forensic export (authorised)' : primaryLabel}
        </a>
        {e.placeholders.length > 0 && !malware && (
          <a href={href('segments')} download title="Only the observed byte segments plus a manifest: no placeholder bytes"
            className={linkCls('border border-line2 bg-card font-medium text-ink hover:bg-tealsoft')}>
            <FileArchive className="h-4 w-4" />Observed segments only (ZIP)
          </a>
        )}
        {f.repaired_copy && !malware && (
          <a href={href('repaired')} download title={`Derived copy: ${f.repaired_copy.method}`}
            className={linkCls('border border-accent/60 bg-[#fff4e0] font-medium text-amber-950 hover:brightness-95')}>
            <Wrench className="h-4 w-4" />Repaired copy (derived)
          </a>
        )}
        {!primaryDisabled && (
          <button disabled={!!busy} onClick={() => verify('export')} title="Fetch the export again, re-hash it in the browser and record the result in the audit trail"
            className="inline-flex items-center gap-1.5 rounded-full border border-line2 bg-card px-4 py-2 text-sm font-medium text-ink hover:bg-tealsoft disabled:opacity-40">
            {busy === 'export' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Fingerprint className="h-4 w-4" />}Verify SHA-256 in browser
          </button>
        )}
        {!publicDemo && !primaryDisabled && (malware ? authorized : true) && (
          <button disabled={restoring} onClick={doRestore} title="Copy the export to a safe local folder. The original evidence is never touched."
            className="inline-flex items-center gap-1.5 rounded-full border border-teal/50 bg-tealsoft px-4 py-2 text-sm font-semibold text-brand-deep hover:brightness-95 disabled:opacity-40">
            {restoring ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderOutput className="h-4 w-4" />}Restore to folder
          </button>
        )}
      </div>
      {!publicDemo && !primaryDisabled && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={useCustomDest} onChange={x => setUseCustomDest(x.target.checked)} />
            Restore to a custom folder instead of <span className="font-mono">~/RecoveryLens_Recovered/</span>
          </label>
          {useCustomDest && <input value={customDest} onChange={x => setCustomDest(x.target.value)} placeholder="C:\Recovery\Case-0001"
            className="min-w-[220px] flex-1 rounded-lg border border-line2 bg-soft px-2.5 py-1 font-mono text-xs text-ink" />}
        </div>
      )}
      {e.placeholders.length > 0 && (
        <div className="rounded-xl border border-line bg-soft p-3 text-xs text-ink2">
          <div className="font-bold text-danger">FORENSIC RECONSTRUCTION ARTIFACT – NOT A VERIFIED RECOVERED FILE</div>
          <p className="mt-1">These byte ranges were NOT recovered. In the artifact they are 0x00 placeholders, declared here and in the manifest; they are not evidence:</p>
          <ul className="mt-1 font-mono text-[11px]">{e.placeholders.slice(0, 8).map((m, i) => (
            <li key={i}>Missing: 0x{m.start.toString(16).toUpperCase().padStart(8, '0')} – 0x{(m.end - 1).toString(16).toUpperCase().padStart(8, '0')} ({m.length.toLocaleString()} bytes)</li>))}</ul>
        </div>
      )}
      {err != null && <ErrorBox title="Verification failed" error={err} />}
      {res && (
        <div className={`rounded-xl border p-3 text-xs ${res.identical ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-red-300 bg-red-50 text-red-900'}`}>
          <div className="flex items-center gap-1.5 font-bold"><Fingerprint className="h-4 w-4" />{res.identical ? 'BYTE-IDENTICAL' : 'HASH MISMATCH'}: {res.name}</div>
          <div className="mt-1 font-mono">Reconstruction SHA-256: {res.expected}</div>
          <div className="font-mono">Browser re-hash SHA-256: {res.clientSha}</div>
          <div className="mt-1 opacity-80">{res.size.toLocaleString()} bytes; the client-side re-hash was written to the audit trail.</div>
        </div>
      )}
      {restoreResult && (
        <div className={`rounded-xl border p-3 text-xs ${restoreResult.byte_identical ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-red-300 bg-red-50 text-red-900'}`}>
          <div className="flex items-center gap-1.5 font-bold">{restoreResult.byte_identical ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
            {restoreResult.byte_identical ? 'RESTORED — BYTE-IDENTICAL' : 'RESTORED — HASH MISMATCH'}</div>
          <div className="mt-1 font-mono">Destination: {restoreResult.destination}</div>
          <div className="font-mono">SHA-256: {restoreResult.sha256}</div>
          <div className="mt-1 opacity-80">Original evidence was not modified. Recorded in the audit trail.</div>
        </div>
      )}
    </div>
  )
}
