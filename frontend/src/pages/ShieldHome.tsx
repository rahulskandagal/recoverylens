import { useEffect, useState } from 'react'
import { AlertTriangle, FolderPlus, HardDrive, ShieldCheck, ShieldOff } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PROTECTION_TONE, shieldApi, type ShieldOverview, type Source } from '../api-shield'
import { Card, ErrorBox, Hint, PageHeader, Spinner, Stat, Tag } from '../components/ui'
import { bytes } from '../lib/format'

export default function ShieldHome() {
  const [ov, setOv] = useState<ShieldOverview | null>(null)
  const [sources, setSources] = useState<Source[] | null>(null)
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)

  const load = () => { shieldApi.overview().then(setOv).catch(setErr); shieldApi.sources().then(setSources).catch(setErr) }
  useEffect(() => { load() }, [])

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!path.trim()) return
    setBusy(true); setErr(null)
    try { await shieldApi.createSource(name.trim(), path.trim()); setName(''); setPath(''); load() } catch (ex) { setErr(ex) } finally { setBusy(false) }
  }

  return (
    <div className="mx-auto max-w-[1300px] space-y-4 px-4 py-6">
      <PageHeader icon={ShieldCheck} accent="#0f4c5c" title="Recovery Shield"
        subtitle="Continuous protection for a local folder: real byte-for-byte recovery points, change detection against the latest protected copy, and safe restoration. Never modifies the source folder except an explicit, confirmed restore-to-original." />
      {err != null && <ErrorBox error={err} />}
      {ov && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          <Stat icon={HardDrive} accent="#0f4c5c" label="Protected sources" value={ov.sources} hint={`${ov.active_protection} actively monitored`} />
          <Stat icon={ShieldCheck} accent="#22a06b" label="Protected files" value={ov.protected_files.toLocaleString()} hint="latest recovery point per source" />
          <Stat icon={HardDrive} accent="#3282b8" label="Recovery points" value={ov.recovery_points} hint="protected snapshots created" />
          <Stat icon={AlertTriangle} accent={ov.incidents ? '#d9534f' : '#1fa6a6'} label="Incidents" value={ov.incidents} hint="new data-loss events raised" />
          <Stat icon={AlertTriangle} accent="#e07b39" label="Deleted detected" value={ov.deleted_detected} hint="across all incidents" />
          <Stat icon={AlertTriangle} accent="#f4a340" label="Corrupted detected" value={ov.corrupted_detected} />
          <Stat icon={ShieldCheck} accent="#22a06b" label="Recoverable" value={ov.recoverable} hint={`${ov.unrecoverable} unrecoverable via Shield`} />
        </div>
      )}
      {ov && <Hint>{ov.message}</Hint>}

      <Card icon={FolderPlus} accent="#f4a340" title="Protect a folder" subtitle="This server must have read access to the folder's path. It is only ever opened read-only; nothing here is modified without an explicit, confirmed restore.">
        <form onSubmit={add} className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-muted">Name<input value={name} onChange={e => setName(e.target.value)} placeholder="My laptop"
            className="mt-0.5 block w-48 rounded-lg border border-line2 bg-soft px-2.5 py-1.5 text-sm text-ink outline-none focus:border-teal" /></label>
          <label className="flex-1 text-xs text-muted">Folder path<input value={path} onChange={e => setPath(e.target.value)} placeholder="C:\Users\me\Documents\SIH_Project" required
            className="mt-0.5 block w-full rounded-lg border border-line2 bg-soft px-2.5 py-1.5 font-mono text-sm text-ink outline-none focus:border-teal" /></label>
          <button disabled={busy} className="inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand-deep disabled:opacity-50">
            <FolderPlus className="h-4 w-4" />Add source</button>
        </form>
      </Card>

      <div className="space-y-3">
        <h2 className="text-sm font-bold uppercase tracking-wider text-ink2">Protected sources</h2>
        {!sources && <Spinner />}
        {sources && !sources.length && <p className="text-sm text-muted">No protected folders yet. Add one above to create your first recovery point.</p>}
        {sources?.map(s => (
          <Link key={s.id} to={`/shield/${s.id}`} className="block rounded-2xl border border-line bg-card p-4 shadow-[var(--shadow-card)] transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)]">
            <div className="flex flex-wrap items-center gap-2">
              {s.protection === 'active' ? <ShieldCheck className="h-5 w-5 text-success" /> : <ShieldOff className="h-5 w-5 text-muted" />}
              <span className="font-semibold text-ink">{s.name}</span>
              <span className="truncate font-mono text-[11px] text-muted">{s.path}</span>
              <Tag tone={PROTECTION_TONE[s.protection] ?? PROTECTION_TONE.inactive}>{s.protection.toUpperCase()}</Tag>
              {!s.path_accessible && <Tag tone="text-red-800 bg-red-50 ring-red-300">PATH NOT ACCESSIBLE</Tag>}
              <span className="ml-auto text-xs text-muted">
                {s.latest_snapshot ? `${s.latest_snapshot.file_count.toLocaleString()} file(s), ${bytes(s.latest_snapshot.total_size)}` : 'no recovery point yet'}
                {s.incident_count > 0 && <span className="ml-2 font-semibold text-danger">{s.incident_count} incident(s)</span>}
              </span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
