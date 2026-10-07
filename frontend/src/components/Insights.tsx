import { useEffect, useState } from 'react'
import { Beaker, Bot, Compass, FileSearch, FileText, GitFork, Network, Puzzle, ShieldCheck, Sigma, Upload, Waypoints } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Link, useNavigate } from 'react-router-dom'
import { adv, GUARD_META, LEVEL_META, type Insights as I } from '../api-advanced'
import Heatmap from './Heatmap'
import { Card, Stat } from './ui'

function MiniBars({ data, color }: { data: { bin: string; n: number }[]; color: string }) {
  return (
    <div className="h-40">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ left: -28, right: 4, top: 6 }}>
          <CartesianGrid stroke="#eee6d6" vertical={false} />
          <XAxis dataKey="bin" tick={{ fontSize: 9, fill: '#6b7c83' }} interval={1} />
          <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: '#6b7c83' }} />
          <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
          <Bar dataKey="n" fill={color} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function CountBars({ counts, meta }: { counts: Record<string, number>; meta: Record<string, { color: string }> }) {
  const data = Object.keys(meta).map(k => ({ k, n: counts[k] ?? 0 }))
  return (
    <div className="h-40">
      <ResponsiveContainer>
        <BarChart data={data} margin={{ left: -28, right: 4, top: 6 }}>
          <CartesianGrid stroke="#eee6d6" vertical={false} />
          <XAxis dataKey="k" tick={{ fontSize: 9, fill: '#6b7c83' }} interval={0} />
          <YAxis allowDecimals={false} tick={{ fontSize: 9, fill: '#6b7c83' }} />
          <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
          <Bar dataKey="n" radius={[3, 3, 0, 0]}>{data.map(d => <Cell key={d.k} fill={meta[d.k].color} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function QuickActions({ caseId }: { caseId: string }) {
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const scan = async () => { setBusy(true); try { await adv.rescan(caseId) } finally { setBusy(false); nav('guardian') } }
  const items: [string, typeof Upload, () => void][] = [
    ['Start analysis', Upload, () => nav('/new')], ['View fragments', Puzzle, () => nav('fragments')], ['View recovered files', FileSearch, () => nav('files')],
    ['View relationships', Network, () => nav('graph')], ['Run security scan', ShieldCheck, scan], ['Run benchmark', Beaker, () => nav('/benchmarks')],
    ['Open AI investigator', Bot, () => nav('investigator')], ['Generate report', FileText, () => nav('report')],
  ]
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
      {items.map(([l, Icon, fn]) => (
        <button key={l} onClick={fn} disabled={busy && l === 'Run security scan'}
          className="flex flex-col items-center gap-1.5 rounded-2xl border border-line bg-card px-2 py-3 text-center text-[11px] font-semibold uppercase tracking-wide text-ink2 shadow-[var(--shadow-card)] transition hover:-translate-y-0.5 hover:border-teal hover:text-brand disabled:opacity-50">
          <span className="grid h-8 w-8 place-items-center rounded-xl bg-tealsoft text-brand"><Icon className="h-4 w-4" /></span>{l}
        </button>
      ))}
    </div>
  )
}

export default function Insights({ caseId }: { caseId: string }) {
  const [d, setD] = useState<I | null>(null)
  const [err, setErr] = useState(false)
  const nav = useNavigate()
  useEffect(() => { adv.insights(caseId).then(setD).catch(() => setErr(true)) }, [caseId])
  if (err) return <p className="text-xs text-muted">Advanced insights: NOT AVAILABLE IN CURRENT ANALYSIS.</p>
  if (!d) return null
  const incomplete = (d.possibility_counts.HIGH ?? 0) + (d.possibility_counts.MEDIUM ?? 0)
  const detections = (d.security_counts['MALWARE DETECTED'] ?? 0) + (d.security_counts.SUSPICIOUS ?? 0)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Stat icon={Sigma} accent="#1fa6a6" label="Average integrity" value={d.average_integrity ?? 'N/A'} hint="mean over all candidates (0–100)" />
        <Stat icon={Waypoints} accent="#3282b8" label="Average reconstruction" value={d.average_reconstruction == null ? 'N/A' : `${d.average_reconstruction}%`}
          hint={d.reconstruction_na ? `${d.reconstruction_na} candidate(s) N/A excluded` : 'all candidates measurable'} />
        <Stat icon={ShieldCheck} accent={detections ? '#d9534f' : '#22a06b'} label="Security detections" value={detections} hint="malware + suspicious (Safety Guardian)" />
        <Stat icon={Compass} accent="#f4a340" label="Recovery possibility" value={incomplete} hint={`artifacts rated HIGH/MEDIUM · LOW ${d.possibility_counts.LOW ?? 0} · UNKNOWN ${d.possibility_counts.UNKNOWN ?? 0}`} />
        <Stat icon={GitFork} accent="#7a5bc7" label="Relationships" value={d.relationship_count} hint={`${d.artifact_relationships} cross-artifact (inferred)`} />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card title="Reconstruction distribution" subtitle="Candidates per reconstruction band (N/A excluded)"><MiniBars data={d.reconstruction_hist} color="#3282b8" /></Card>
        <Card title="Relationship confidence" subtitle="Recorded relationship edges per confidence band"><MiniBars data={d.relationship_conf_hist} color="#7a5bc7" /></Card>
        <Card title="Recovery possibility" subtitle={<Link to="possibility" className="text-brand hover:underline">Open the possibility map →</Link>}>
          <CountBars counts={d.possibility_counts} meta={LEVEL_META} /></Card>
        <Card title="Security status" subtitle={<Link to="guardian" className="text-brand hover:underline">Open the Safety Guardian →</Link>}>
          <CountBars counts={d.security_counts} meta={GUARD_META} /></Card>
      </div>
      <Card title="Storage heatmap" subtitle="Recovered, candidate, uncertain and corrupted regions on the storage image. Click a coloured cell to open its artifact.">
        <Heatmap h={d.heatmap} onPick={o => nav(o.startsWith('R') ? `files/${o}` : `dna?frag=${o}`)} />
      </Card>
    </div>
  )
}
