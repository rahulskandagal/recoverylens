import { Suspense, useEffect, useState } from 'react'
import {
  Activity, Beaker, Bot, Compass, Dna, FileSearch, FileText, FlaskConical, Gauge, HardDrive, ListTree, Network, Scale, ScrollText,
  Share2, ShieldAlert, ShieldCheck, ShieldHalf, SlidersHorizontal, Workflow,
} from 'lucide-react'
import { NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import { api, type CaseDetail } from '../api'
import { bytes } from '../lib/format'
import { DemoBanner, ErrorBox, Spinner } from './ui'

export type CaseCtx = { c: CaseDetail; reload: () => void }

export default function CaseLayout() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [c, setC] = useState<CaseDetail | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const load = () => api.case(id).then(setC).catch(setErr)
  useEffect(() => { load() }, [id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (c && (c.status === 'running' || c.status === 'queued')) nav(`/cases/${id}/progress`, { replace: true })
  }, [c, id, nav])
  if (err) return <div className="mx-auto max-w-4xl p-6"><ErrorBox title="Case unavailable" error={err} /></div>
  if (!c) return <Spinner />
  if (c.status === 'failed') return <div className="mx-auto max-w-4xl p-6"><ErrorBox title="Analysis failed" error={c.error} /></div>
  const sec = c.summary?.security
  const flagged = sec ? (sec.counts.MALWARE_DETECTED ?? 0) + (sec.counts.SUSPICIOUS ?? 0) : 0
  const groups: { title: string; items: { to: string; icon: typeof Gauge; label: string; end?: boolean }[] }[] = [
    { title: 'Analysis', items: [
      { to: '', icon: Gauge, label: 'Dashboard', end: true },
      { to: 'fragments', icon: Activity, label: 'Fragments' },
      { to: 'dna', icon: Dna, label: 'Fragment DNA' },
      { to: 'files', icon: FileSearch, label: 'Recovered files' },
    ] },
    { title: 'Reconstruction', items: [
      { to: 'twin', icon: Workflow, label: 'Recovery Digital Twin' },
      { to: 'graph', icon: Network, label: 'Relationship graph' },
      { to: 'structure', icon: ListTree, label: 'Structure Explorer' },
      { to: 'possibility', icon: Compass, label: 'Recovery Possibility' },
    ] },
    { title: 'Intelligence', items: [
      { to: 'guardian', icon: ShieldHalf, label: 'Security Guardian' },
      { to: 'cross', icon: Share2, label: 'Cross-Artifact Intelligence' },
      { to: 'investigator', icon: Bot, label: 'AI Investigator' },
    ] },
    { title: 'Validation', items: [
      { to: '/benchmarks', icon: Beaker, label: 'Benchmark Lab' },
      { to: '/calibration', icon: Scale, label: 'Confidence calibration' },
      ...(c.evaluation ? [{ to: 'evaluation', icon: FlaskConical, label: 'Ground-truth check' }] : []),
      { to: 'priorities', icon: SlidersHorizontal, label: 'Priority criteria' },
    ] },
    { title: 'Records', items: [
      { to: 'audit', icon: ScrollText, label: 'Audit log' },
      { to: 'report', icon: FileText, label: 'Reports' },
    ] },
  ]
  const items = groups.flatMap(g => g.items)
  return (
    <div className="mx-auto flex max-w-[1500px] gap-6 px-4 py-6">
      <aside className="sticky top-20 hidden h-[calc(100vh-6.5rem)] w-60 shrink-0 flex-col lg:flex">
        <div className="rounded-2xl border border-line bg-card p-4 shadow-[var(--shadow-card)]">
          <div className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand text-white"><HardDrive className="h-4.5 w-4.5" /></span>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-ink" title={c.name}>{c.name}</div>
              <div className="truncate font-mono text-[11px] text-muted">{c.image_name} · {bytes(c.image_size)}</div>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide">
            {c.mode === 'demo' && <span className="rounded-full bg-accent px-2 py-0.5 text-white">Demo mode</span>}
            <span className="rounded-full bg-teal/15 px-2 py-0.5 text-brand" title={c.summary?.input_type?.evidence.join('; ')}>
              {c.summary?.input_type?.label ?? (c.mode === 'demo' ? 'Raw storage image' : 'Uploaded input')}</span>
          </div>
          <div className={`mt-3 flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium ${c.evidence_unchanged ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-900'}`} title={`SHA-256 ${c.image_sha256}`}>
            <ShieldCheck className="h-3.5 w-3.5" />{c.evidence_unchanged ? 'Evidence hash verified unchanged' : 'Evidence hash recorded'}
          </div>
          {sec && (
            <div className={`mt-1.5 flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium ${flagged ? 'bg-red-50 text-red-800' : 'bg-tealsoft text-brand'}`}
              title={`Engine: ${sec.engine ?? 'none available'}; rules: ${sec.rule_engine}`}>
              <ShieldAlert className="h-3.5 w-3.5" />
              {flagged ? `${flagged} artifact(s) flagged by security scan` : sec.engine ? 'No security detections' : 'No antivirus engine available'}
            </div>
          )}
        </div>
        <nav className="mt-3 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pr-1 scroll-thin">
          {groups.map(g => (
            <div key={g.title} className="mb-1.5">
              <div className="px-3.5 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.14em] text-muted">{g.title}</div>
              {g.items.map(it => (
                <NavLink key={it.to} to={it.to} end={it.end}
                  className={({ isActive }) => `flex items-center gap-2.5 rounded-xl px-3 py-1.5 text-[13px] font-medium transition ${isActive ? 'bg-brand text-white shadow-[var(--shadow-card)]' : 'text-ink2 hover:bg-card hover:text-brand hover:shadow-[var(--shadow-card)]'}`}>
                  {({ isActive }) => <><span className={`grid h-6.5 w-6.5 shrink-0 place-items-center rounded-lg ${isActive ? 'bg-white/15' : 'bg-tealsoft text-brand'}`}><it.icon className="h-3.5 w-3.5" /></span>{it.label}</>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1 space-y-5">
        <nav className="flex gap-1 overflow-x-auto lg:hidden">
          {items.map(it => (
            <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => `whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-medium ${isActive ? 'bg-brand text-white' : 'bg-card text-ink2'}`}>{it.label}</NavLink>
          ))}
        </nav>
        {c.mode === 'demo' && <DemoBanner />}
        <Suspense fallback={<Spinner />}><Outlet context={{ c, reload: load } satisfies CaseCtx} /></Suspense>
      </main>
    </div>
  )
}
