import { Fragment, useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, ArrowRight, Database, Download, Eye, FileImage, FileText, FileWarning, Fingerprint, FolderOutput,
  HardDrive, Loader2, RefreshCw, Search, ShieldAlert, ShieldCheck, ShieldX, Trash2, Upload, Usb,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api, type Dataset } from '../api'
import { realApi, type CarveImage, type CarveResult, type Drive, type RecycleScan } from '../api-real'
import { Card, ErrorBox, Hint } from '../components/ui'
import { bytes, sha256Hex } from '../lib/format'

const ICONS: Record<string, typeof Usb> = { REAL: HardDrive, USB: Usb, A: FileImage, B: FileText, C: FileText, D: Database, E: FileWarning, SEC1: ShieldCheck, SEC2: ShieldAlert, SEC3: ShieldX }
const ACCENT: Record<string, string> = { REAL: '#f4a340', USB: '#0f4c5c', A: '#1baf7a', B: '#2a78d6', C: '#eb6834', D: '#eda100', E: '#d9534f', SEC1: '#22a06b', SEC2: '#f4a340', SEC3: '#d9534f' }

function SectionLabel({ tone, children }: { tone: 'demo' | 'real'; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`rounded-full px-3 py-1 text-[11px] font-bold uppercase tracking-widest ${tone === 'demo' ? 'bg-accent text-white' : 'bg-brand text-white'}`}>{children}</span>
      <div className={`h-px flex-1 ${tone === 'demo' ? 'bg-accent/30' : 'bg-brand/30'}`} />
    </div>
  )
}

function RecycleBinCard() {
  const nav = useNavigate()
  const [drives, setDrives] = useState<Drive[] | null>(null)
  const [drive, setDrive] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [scan, setScan] = useState<RecycleScan | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [images, setImages] = useState<CarveImage[] | null>(null)
  const [carveOpen, setCarveOpen] = useState<string | null>(null)
  const [carveCase, setCarveCase] = useState<Record<string, string>>({})
  const [carveBusy, setCarveBusy] = useState<string | null>(null)
  const [carveResult, setCarveResult] = useState<Record<string, CarveResult>>({})
  useEffect(() => { realApi.drives().then(ds => { setDrives(ds); setDrive(ds.find(d => d.has_recycle_bin)?.drive ?? ds[0]?.drive ?? '') }).catch(setErr) }, [])
  useEffect(() => { realApi.carveImages().then(setImages).catch(() => setImages([])) }, [])

  const runScan = async () => {
    if (!drive) return
    setBusy('scan'); setErr(null); setScan(null)
    try { setScan(await realApi.scan(drive)) } catch (e) { setErr(e) } finally { setBusy(null) }
  }
  const recover = async (itemId: string) => {
    setBusy(itemId); setErr(null)
    try { const { case_id } = await realApi.recover(scan!.id, itemId); nav(`/cases/${case_id}/progress`) } catch (e) { setErr(e); setBusy(null) }
  }
  const toggleCarve = (itemId: string) => setCarveOpen(o => o === itemId ? null : itemId)
  const runCarve = async (itemId: string) => {
    const caseId = carveCase[itemId]
    if (!caseId) return
    setCarveBusy(itemId); setErr(null)
    try {
      const res = await realApi.carve(scan!.id, itemId, caseId)
      setCarveResult(r => ({ ...r, [itemId]: res }))
    } catch (e) { setErr(e) } finally { setCarveBusy(null) }
  }

  return (
    <Card title="Recycle Bin evidence" subtitle="Reads the actual $I/$R records Windows leaves behind when a file is deleted to the Recycle Bin. Read-only: nothing here is modified, moved or removed." icon={Trash2} accent="#0f4c5c">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-muted">Drive
          <select value={drive} onChange={e => setDrive(e.target.value)} className="mt-0.5 block min-w-[160px] rounded-lg border border-line2 bg-soft px-2.5 py-1.5 font-mono text-sm text-ink outline-none focus:border-teal">
            {!drives && <option>Loading…</option>}
            {drives?.map(d => <option key={d.drive} value={d.drive}>{d.drive} {d.has_recycle_bin ? '' : '(no Recycle Bin found)'}</option>)}
          </select></label>
        <button onClick={runScan} disabled={!drive || !!busy} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">
          {busy === 'scan' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}Scan Recycle Bin</button>
      </div>
      {err != null && <div className="mt-2"><ErrorBox error={err} /></div>}
      {scan && (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>{scan.item_count} deleted {scan.item_count === 1 ? 'entry' : 'entries'} found on {scan.drive}</span>
            <span className="font-semibold text-success">{scan.recoverable_count} recoverable now</span>
            {scan.item_count - scan.recoverable_count > 0 && <span className="font-semibold text-danger">{scan.item_count - scan.recoverable_count} permanently removed (Recycle Bin already emptied for these)</span>}
          </div>
          {scan.errors.length > 0 && <p className="text-[11px] text-muted">{scan.errors.join(' · ')}</p>}
          {scan.items.length === 0 ? (
            <p className="text-xs text-muted">No deleted-file records found in this drive's Recycle Bin right now.</p>
          ) : (
            <div className="max-h-72 overflow-y-auto rounded-xl border border-line scroll-thin">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-[#f7efe0] text-left text-[10px] uppercase tracking-wider text-ink2"><tr><th className="px-3 py-2">File</th><th>Deleted</th><th>Size</th><th>Evidence</th><th className="pr-3 text-right">Action</th></tr></thead>
                <tbody>{scan.items.map(it => {
                  const res = carveResult[it.item_id]
                  return (
                  <Fragment key={it.item_id}>
                  <tr className="border-t border-line">
                    <td className="px-3 py-1.5"><div className="font-mono text-ink">{it.original_name}</div><div className="truncate text-[10px] text-muted" title={it.original_path}>{it.original_path}</div></td>
                    <td className="text-muted">{it.deleted_at ? it.deleted_at.slice(0, 19).replace('T', ' ') : 'unknown'}</td>
                    <td className="text-muted">{bytes(it.size)}</td>
                    <td>{it.has_data
                      ? <span className="inline-flex items-center gap-1 font-semibold text-success"><ShieldCheck className="h-3.5 w-3.5" />Recycle Bin Evidence Found</span>
                      : <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle className="h-3.5 w-3.5" />Data purged</span>}</td>
                    <td className="pr-3 text-right">
                      {it.case_id ? <button onClick={() => nav(`/cases/${it.case_id}`)} className="rounded-full bg-tealsoft px-3 py-1 text-[11px] font-semibold text-brand">Open recovered file</button>
                        : it.has_data ? <button disabled={!!busy} onClick={() => recover(it.item_id)} className="inline-flex items-center gap-1 rounded-full bg-accent px-3 py-1 text-[11px] font-bold text-brand-deep disabled:opacity-50">
                          {busy === it.item_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowRight className="h-3.5 w-3.5" />}Recover &amp; analyse</button>
                          : <button onClick={() => toggleCarve(it.item_id)} className="inline-flex items-center gap-1 rounded-full bg-tealsoft px-3 py-1 text-[11px] font-bold text-brand">
                            <Search className="h-3.5 w-3.5" />Recover &amp; Analyse</button>}
                    </td>
                  </tr>
                  {!it.has_data && !it.case_id && carveOpen === it.item_id && (
                    <tr className="border-t border-line bg-soft/60">
                      <td colSpan={5} className="px-3 py-3">
                        {images === null ? (
                          <span className="inline-flex items-center gap-1.5 text-[11px] text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading available storage images…</span>
                        ) : images.length === 0 ? (
                          <p className="text-[11px] text-ink2">Upload a storage image below to attempt recovery of this permanently deleted file. Nothing can be recovered without a copy of the drive's actual bytes.</p>
                        ) : !res ? (
                          <div className="flex flex-wrap items-center gap-2">
                            <label className="text-[11px] text-muted">Search in
                              <select value={carveCase[it.item_id] ?? ''} onChange={e => setCarveCase(c => ({ ...c, [it.item_id]: e.target.value }))}
                                className="ml-1.5 rounded-lg border border-line2 bg-card px-2 py-1 font-mono text-[11px] text-ink outline-none focus:border-teal">
                                <option value="">Choose an analysed storage image…</option>
                                {images.map(im => <option key={im.id} value={im.id}>{im.name} · {bytes(im.image_size)}</option>)}
                              </select></label>
                            <button disabled={!carveCase[it.item_id] || carveBusy === it.item_id} onClick={() => runCarve(it.item_id)}
                              className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 text-[11px] font-bold text-brand-deep disabled:opacity-50">
                              {carveBusy === it.item_id ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />Searching storage evidence…</> : <>Search evidence</>}
                            </button>
                          </div>
                        ) : (
                          <div className="space-y-1.5">
                            {res.status === 'RECOVERED' && <p className="inline-flex items-center gap-1.5 text-[12px] font-bold text-success"><ShieldCheck className="h-4 w-4" />Recovered</p>}
                            {res.status === 'PARTIALLY_RECOVERABLE' && <p className="inline-flex items-center gap-1.5 text-[12px] font-bold text-amber-700"><AlertTriangle className="h-4 w-4" />Partially Recoverable</p>}
                            {res.status === 'INSUFFICIENT_EVIDENCE' && <p className="inline-flex items-center gap-1.5 text-[12px] font-bold text-danger"><ShieldX className="h-4 w-4" />Insufficient Evidence</p>}
                            <p className="max-w-2xl text-[11px] text-ink2">{res.reason}</p>
                            {res.status !== 'INSUFFICIENT_EVIDENCE' && res.file_id && (
                              <div className="flex flex-wrap gap-2 pt-1">
                                <a href={`/api/cases/${res.case_id}/files/${res.file_id}/preview`} target="_blank" rel="noreferrer"
                                  className="inline-flex items-center gap-1 rounded-full border border-line2 bg-card px-3 py-1 text-[11px] font-semibold text-ink hover:border-teal"><Eye className="h-3.5 w-3.5" />Preview</a>
                                <a href={`/api/cases/${res.case_id}/files/${res.file_id}/download?variant=export`}
                                  className="inline-flex items-center gap-1 rounded-full border border-line2 bg-card px-3 py-1 text-[11px] font-semibold text-ink hover:border-teal"><Download className="h-3.5 w-3.5" />Download</a>
                                <button onClick={() => nav(`/cases/${res.case_id}/files/${res.file_id}#export`)}
                                  className="inline-flex items-center gap-1 rounded-full bg-tealsoft px-3 py-1 text-[11px] font-semibold text-brand"><FolderOutput className="h-3.5 w-3.5" />Restore</button>
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                  </Fragment>
                  )})}</tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

export default function NewAnalysis() {
  const nav = useNavigate()
  const [ds, setDs] = useState<Dataset[]>([])
  const [err, setErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [keywords, setKeywords] = useState('project, aurora, budget')
  const [file, setFile] = useState<File | null>(null)
  const [fileSha, setFileSha] = useState<string | null>(null)
  const [hashing, setHashing] = useState(false)
  const [auth, setAuth] = useState(false)
  // null = not known yet. Rendering neither branch (rather than defaulting to false/local-mode)
  // avoids briefly flashing the Real Recovery Mode UI -- with its own live API calls -- before
  // the config fetch resolves, which caused a layout reflow right as a page-load click landed.
  const [publicDemo, setPublicDemo] = useState<boolean | null>(null)
  useEffect(() => { api.datasets().then(setDs).catch(setErr) }, [])
  useEffect(() => { api.config().then(c => setPublicDemo(c.public_demo)).catch(() => setPublicDemo(false)) }, [])

  const pickFile = async (f: File | null) => {
    setFile(f); setFileSha(null)
    if (!f) return
    setHashing(true)
    try { setFileSha(await sha256Hex(await f.arrayBuffer())) } catch { /* WebCrypto unavailable; SHA-256 will still be computed server-side */ } finally { setHashing(false) }
  }

  const startDemo = async (key: string) => {
    setBusy(true)
    try {
      const kw = keywords.split(',').map(s => s.trim()).filter(Boolean)
      const { id } = await api.createDemo(key, { keywords: kw } as never)
      nav(`/cases/${id}/progress`)
    } catch (e) { setErr(e); setBusy(false) }
  }
  const startUpload = async () => {
    if (!file) return
    setBusy(true)
    try {
      const { id } = await api.upload(file, auth)
      nav(`/cases/${id}/progress`)
    } catch (e) { setErr(e); setBusy(false) }
  }

  return (
    <div className="mx-auto max-w-[1200px] space-y-5 px-4 py-6">
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-ink">New analysis</h1>
        <p className="mt-1 text-sm text-muted">Two separate modes, never mixed: prepared <b>Demo Mode</b> scenarios for demonstration, or <b>Real Recovery Mode</b> against your own actual evidence.</p>
      </div>
      {err != null && <ErrorBox error={err} />}

      {publicDemo === null ? (
        <SectionLabel tone="real">Real Recovery Mode</SectionLabel>
      ) : publicDemo ? (
        <>
          <SectionLabel tone="real">Real Recovery Mode</SectionLabel>
          <Hint>Disabled in this public demo: Real Recovery Mode reads a machine's own local drives and Recycle Bin, which only makes sense run locally on your own machine. Clone the repo and run it locally (see the README) to use it against your own evidence.</Hint>
        </>
      ) : (
        <>
          <SectionLabel tone="real">Real Recovery Mode</SectionLabel>
          <Hint>Analysis only ever reads evidence read-only. Avoid writing new data to the affected drive — new writes may overwrite recoverable deleted data.</Hint>
          <RecycleBinCard />
          <Card title="Upload a storage image" subtitle="Raw/dd image of a USB stick, memory card or disk (≤ 1 GiB in this prototype), or a single file (e.g. a corrupted PDF/JPEG/DOCX). Direct raw-device access from a browser is intentionally not supported — see the README for the optional native acquisition script.">
            <div className="grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
              <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line2 bg-soft px-4 py-8 text-sm text-muted hover:border-teal/60">
                <Upload className="h-6 w-6 text-muted" />
                {file ? <span className="font-mono text-ink">{file.name} · {bytes(file.size)}</span> : <span>Choose an .img / .dd / .raw / .bin / any file</span>}
                <input type="file" className="hidden" onChange={e => pickFile(e.target.files?.[0] ?? null)} />
              </label>
              <div className="space-y-3">
                <label className="flex max-w-sm items-start gap-2 text-xs text-ink2">
                  <input type="checkbox" checked={auth} onChange={e => setAuth(e.target.checked)} className="mt-0.5" />
                  I confirm I am authorised to analyse this storage image and its contents.
                </label>
                <button disabled={!file || !auth || busy} onClick={startUpload}
                  className="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-deep disabled:cursor-not-allowed disabled:opacity-40">
                  START REAL ANALYSIS
                </button>
              </div>
            </div>
            {file && (
              <div className="mt-3 rounded-xl border border-line2 bg-soft p-3 font-mono text-[11px] text-ink2">
                <div><b className="text-ink">Filename:</b> {file.name}</div>
                <div><b className="text-ink">Size:</b> {bytes(file.size)} ({file.size.toLocaleString()} bytes)</div>
                <div className="flex items-center gap-1.5"><Fingerprint className="h-3 w-3 text-teal" /><b className="text-ink">SHA-256:</b> {hashing ? 'computing…' : fileSha ?? 'will be computed on the server'}</div>
                <div><b className="text-ink">Evidence ID:</b> assigned after upload (shown on the next screen)</div>
                <div><b className="text-ink">Read-only:</b> YES — copied to a write-protected evidence store before analysis</div>
              </div>
            )}
            <div className="mt-3"><Hint>Real evidence never uses demo data: fragment counts, sizes and recovery percentages all come from this exact file, not from a synthetic dataset.</Hint></div>
          </Card>
        </>
      )}

      <SectionLabel tone="demo">Demo Mode</SectionLabel>
      <Card title="Investigation criteria" subtitle="Used by the priority engine; can be changed after analysis.">
        <label className="text-xs text-muted">Keywords (comma separated)</label>
        <input value={keywords} onChange={e => setKeywords(e.target.value)}
          className="mt-1 w-full rounded-lg border border-line2 bg-soft px-3 py-2 font-mono text-sm text-ink outline-none focus:border-teal" />
      </Card>
      {[['Recovery demos', 'Synthetic images containing real file formats, fragmented and damaged on purpose. Clearly labelled as simulated everywhere.', (d: Dataset) => d.group !== 'security'],
        ['Security demos', 'Harmless scenarios for the malware-analysis layer: clean, suspicious (inert PE header stub + auto-run PDF JavaScript) and a SIMULATED test-signature detection. No real malware is included.', (d: Dataset) => d.group === 'security']].map(([title, sub, pred]) => (
        <Card key={title as string} title={title as string} subtitle={sub as string}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {ds.filter(pred as (d: Dataset) => boolean).map(d => {
              const Icon = ICONS[d.key] ?? HardDrive
              const ac = ACCENT[d.key] ?? '#1fa6a6'
              return (
                <button key={d.key} disabled={busy} onClick={() => startDemo(d.key)}
                  className={`group relative overflow-hidden rounded-2xl border bg-card p-4 text-left shadow-[var(--shadow-card)] transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] disabled:opacity-50 ${d.featured ? 'border-accent ring-2 ring-accent/40' : 'border-line'}`}>
                  <div className="absolute inset-y-0 left-0 w-1.5" style={{ background: ac }} />
                  {d.featured && <span className="absolute right-3 top-3 rounded-full bg-accent px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-deep">Recommended for demo</span>}
                  <div className="flex items-center gap-2.5">
                    <span className="grid h-9 w-9 place-items-center rounded-xl" style={{ background: `${ac}1f`, color: ac }}><Icon className="h-5 w-5" /></span>
                    <span className="font-semibold text-ink">{d.title}</span>
                  </div>
                  <p className="mt-2 text-sm text-muted">{d.summary}</p>
                  <div className="mt-3 flex items-center gap-2 text-[11px]">
                    <span className="rounded-full bg-accent px-2 py-0.5 font-bold text-white">DEMO · SYNTHETIC</span>
                    <span className="text-muted">{bytes(d.size_bytes)} image</span>
                    <span className="ml-auto font-semibold text-brand opacity-0 transition group-hover:opacity-100">Analyse →</span>
                  </div>
                </button>
              )
            })}
          </div>
        </Card>
      ))}
    </div>
  )
}
