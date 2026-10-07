import { useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, Circle, Loader2 } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type CaseDetail } from '../api'
import { Card, DemoBanner, ErrorBox } from '../components/ui'

const STAGES = [
  { key: 'ingest', label: 'Evidence intake & hashing', at: 0.0 },
  { key: 'scan', label: 'Cluster scan & fragment detection', at: 0.05 },
  { key: 'identify', label: 'Signature & metadata identification', at: 0.26 },
  { key: 'relate', label: 'Relationship analysis & reconstruction', at: 0.3 },
  { key: 'security', label: 'Security sweep (static rules / antivirus)', at: 0.62 },
  { key: 'validate', label: 'Format validation, security scan & export check', at: 0.65 },
  { key: 'score', label: 'Scoring, classification & priority', at: 0.8 },
  { key: 'report', label: 'Provenance, report & evidence re-hash', at: 0.92 },
]

export default function Progress() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [c, setC] = useState<CaseDetail | null>(null)
  const [log, setLog] = useState<{ id: number; ts: string; stage: string; message: string }[]>([])
  const [err, setErr] = useState<unknown>(null)
  const after = useRef(0)
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const [cc, lines] = await Promise.all([api.case(id), api.audit(id, after.current)])
        if (!alive) return
        setC(cc)
        if (lines.length) {
          after.current = lines[lines.length - 1].id
          setLog(l => [...l, ...lines])
        }
        if (cc.status === 'complete') { setTimeout(() => alive && nav(`/cases/${id}`), 1400); return }
        if (cc.status === 'failed') return
        setTimeout(tick, 350)
      } catch (e) { setErr(e) }
    }
    tick()
    return () => { alive = false }
  }, [id, nav])
  const p = c?.progress ?? 0
  const done = c?.status === 'complete'
  return (
    <div className="mx-auto max-w-[1100px] space-y-4 px-4 py-6">
      {c?.mode === 'demo' && <DemoBanner />}
      {err != null && <ErrorBox error={err} />}
      {c?.status === 'failed' && <ErrorBox error={`Analysis failed: ${c.error}`} />}
      <div>
        <h1 className="text-2xl font-semibold text-ink">{c?.name ?? 'Analysis'}</h1>
        <p className="font-mono text-xs text-muted">{c?.image_name} · SHA-256 {c?.image_sha256}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-[340px_1fr]">
        <Card title="Pipeline">
          <div className="mb-4 h-2 overflow-hidden rounded-full bg-track">
            <motion.div className="h-full bg-gradient-to-r from-teal to-accent" animate={{ width: `${Math.max(3, p * 100)}%` }} />
          </div>
          <ol className="space-y-2.5">
            {STAGES.map((s, i) => {
              const next = STAGES[i + 1]?.at ?? 1.01
              const state = done || p >= next ? 'done' : p >= s.at ? 'active' : 'todo'
              return (
                <li key={s.key} className="flex items-center gap-2.5 text-sm">
                  {state === 'done' ? <CheckCircle2 className="h-4 w-4 text-success" /> : state === 'active' ? <Loader2 className="h-4 w-4 animate-spin text-brand" /> : <Circle className="h-4 w-4 text-muted/70" />}
                  <span className={state === 'todo' ? 'text-muted' : 'text-ink'}>{s.label}</span>
                </li>
              )
            })}
          </ol>
          <div className="mt-4 rounded-md bg-soft px-3 py-2 font-mono text-xs text-muted">{done ? 'Complete — opening dashboard…' : c?.stage}</div>
        </Card>
        <Card title="Live audit trail">
          <div className="scroll-thin max-h-[440px] space-y-1 overflow-y-auto font-mono text-xs">
            {log.map(l => (
              <motion.div key={l.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} className="flex gap-3">
                <span className="shrink-0 text-muted/70">{l.ts.slice(11, 19)}</span>
                <span className="w-20 shrink-0 text-brand">{l.stage}</span>
                <span className="text-ink2">{l.message}</span>
              </motion.div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
