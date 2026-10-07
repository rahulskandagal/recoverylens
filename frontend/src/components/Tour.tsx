import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Loader2, Mic, Presentation, X } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type FileRow } from '../api'

/** Guided demo: a scripted walk through the strongest story, with what to show and what to say. */

type Ids = { minutes?: string; report?: string; docx?: string; photo?: string }
type Step = { title: string; path: (c: string, ids: Ids) => string; show: string; say: string }

const STEPS: Step[] = [
  { title: 'Evidence in, untouched', path: c => `/cases/${c}`,
    show: 'Sidebar: "Evidence hash verified unchanged". Stat cards: 5 of 8 candidates recovered.',
    say: 'We load an 18 MB damaged USB image. It is copied read-only and hashed; after analysis it is re-hashed to prove we never changed it.' },
  { title: 'What was found', path: c => `/cases/${c}/files`,
    show: 'The files table: real names recovered from deleted directory entries, separate Integrity / Reconstruction / Confidence bars.',
    say: 'Seven deleted files came back with their real names. We never collapse quality into one number: integrity, reconstruction and confidence are separate.' },
  { title: 'Proof, not a guess', path: (c, i) => `/cases/${c}/files/${i.minutes ?? ''}`,
    show: 'Section 7 Provenance: every byte range points to its source offset. Section 5: all validator checks pass.',
    say: 'This PDF was scattered in three pieces. Each piece is placed by the file\'s own index: object headers sit exactly at their xref offsets, and startxref points exactly at the xref table.' },
  { title: 'Recovery Digital Twin', path: (c, i) => `/cases/${c}/twin?file=${i.report ?? ''}`,
    show: 'Click "Replay reconstruction". Point at the red MISSING RANGE node, then the Validation node.',
    say: 'Here is how this report was rebuilt, fragment by fragment. One piece was overwritten - we show the gap in red and never fill it with invented data.' },
  { title: 'Can we get more back?', path: (c, i) => `/cases/${c}/possibility?file=${i.report ?? ''}`,
    show: 'Click the red hatched region, then "Simulate" a candidate. Point at SIMULATION — NOT EVIDENCE and the verdict.',
    say: 'The possibility map says whether more recovery is realistic, from evidence rules. The simulator tests a candidate on a temporary copy - a wrong fragment is rejected by the validators.' },
  { title: 'Inside the file', path: (c, i) => `/cases/${c}/structure?file=${i.docx ?? ''}`,
    show: 'Byte layout bar and tree: every member of the Word file, its offset, source fragment and CRC result.',
    say: 'Deterministic parsers, not an LLM, decide validity. Six members pass CRC-32; the embedded picture has real bit-rot and is flagged corrupted, not hidden.' },
  { title: 'Safe to open?', path: c => `/cases/${c}/guardian`,
    show: 'Status wording: never "safe" - "no malware detected by available scanners" plus the caveat.',
    say: 'Every recovered file is scanned statically before export. Nothing is ever executed, and an absence of detection is never presented as proof of safety.' },
  { title: 'How files relate', path: c => `/cases/${c}/cross`,
    show: 'Click a line: type, evidence and confidence. Every link is labelled INFERRED.',
    say: 'Cross-artifact intelligence links files by shared identifiers, metadata, timestamps and content - each with its evidence, never as a conclusion.' },
  { title: 'AI Investigator', path: c => `/cases/${c}/investigator`,
    show: 'Generate suggested next steps, open VIEW EVIDENCE, APPROVE one. Show the audit log below.',
    say: 'The AI proposes next steps from structured evidence only. Nothing runs without approval, and every decision is logged.' },
  { title: 'Is it accurate?', path: () => '/benchmarks',
    show: 'RUN BENCHMARK: the pipeline stages animate; then the metric cards.',
    say: 'We do not ask you to trust us. The benchmark lab damages clean files nine ways and measures recovery against ground truth - labelled synthetic test data.' },
  { title: 'Is the confidence honest?', path: () => '/calibration',
    show: 'Brier score, precision/recall and the calibration curve.',
    say: 'Confidence is calibrated against confirmed outcomes. Where data is insufficient, the system says so instead of inventing statistics.' },
  { title: 'The deliverable', path: c => `/cases/${c}/report`,
    show: 'Click "Forensic PDF report".',
    say: 'Finally, a forensic report: evidence hash, byte-level provenance, validation, security and the full audit trail - generated from the same records as the UI.' },
]

type TourState = { active: boolean; step: number; caseId: string | null; ids: Ids }
type Ctx = { state: TourState; start: () => Promise<void>; stop: () => void; go: (n: number) => void; preparing: string | null }
const TourCtx = createContext<Ctx | null>(null)
const KEY = 'rl-tour'

function load(): TourState {
  try { const s = sessionStorage.getItem(KEY); if (s) return JSON.parse(s) } catch { /* storage unavailable */ }
  return { active: false, step: 0, caseId: null, ids: {} }
}

function pick(files: FileRow[]): Ids {
  const f = (s: string) => files.find(x => x.file_name.startsWith(s))?.file_id
  return { minutes: f('Meeting_Minutes'), report: f('Incident_Report'), docx: f('Recovery_Test_Proposal'), photo: f('site_inspection_photo') }
}

export function TourProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<TourState>(load)
  const [preparing, setPreparing] = useState<string | null>(null)
  const nav = useNavigate()
  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(state)) } catch { /* ignore */ } }, [state])

  const go = useCallback((n: number) => {
    if (!state.caseId) return
    const step = Math.max(0, Math.min(STEPS.length - 1, n))
    nav(STEPS[step].path(state.caseId, state.ids))
    setState({ ...state, step })
  }, [nav, state])

  const start = useCallback(async () => {
    setPreparing('Finding the demo case…')
    try {
      const cases = await api.cases()
      let id = cases.find(c => c.dataset === 'REAL' && c.status === 'complete')?.id
      if (!id) {
        setPreparing('Analysing the damaged USB image (about 20 s)…')
        id = (await api.createDemo('REAL')).id
        nav(`/cases/${id}/progress`)
        for (let i = 0; i < 180; i++) {
          await new Promise(r => setTimeout(r, 1000))
          const c = await api.case(id)
          if (c.status === 'complete') break
          if (c.status === 'failed') throw new Error(c.error ?? 'analysis failed')
        }
      }
      const ids = pick(await api.files(id))
      setState({ active: true, step: 0, caseId: id, ids })
      nav(STEPS[0].path(id, ids))
    } catch (e) {
      alert(`Guided demo could not start: ${(e as Error).message}`)
    } finally { setPreparing(null) }
  }, [nav])

  const stop = useCallback(() => setState(s => ({ ...s, active: false })), [])
  const value = useMemo(() => ({ state, start, stop, go, preparing }), [state, start, stop, go, preparing])
  return <TourCtx.Provider value={value}>{children}<TourPanel /></TourCtx.Provider>
}

export function useTour() {
  const c = useContext(TourCtx)
  if (!c) throw new Error('useTour outside TourProvider')
  return c
}

export function TourButton({ className = '' }: { className?: string }) {
  const { start, preparing, state } = useTour()
  return (
    <button onClick={start} disabled={!!preparing || state.active}
      className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-semibold transition disabled:opacity-60 ${className}`}>
      {preparing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Presentation className="h-4 w-4" />}Guided demo
    </button>
  )
}

function TourPanel() {
  const { state, stop, go, preparing } = useTour()
  const loc = useLocation()
  const st = STEPS[state.step]
  return (
    <AnimatePresence>
      {preparing && (
        <motion.div key="prep" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
          className="fixed bottom-5 right-5 z-50 flex items-center gap-2 rounded-2xl bg-brand px-4 py-3 text-sm text-white shadow-[var(--shadow-lift)]">
          <Loader2 className="h-4 w-4 animate-spin" />{preparing}
        </motion.div>
      )}
      {state.active && st && (
        <motion.aside key="tour" initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }}
          className="fixed bottom-5 right-5 z-50 w-[380px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-teal/40 bg-card shadow-[var(--shadow-lift)]"
          aria-label="Guided demo">
          <div className="flex items-center gap-2 bg-brand px-4 py-2.5 text-white">
            <Presentation className="h-4 w-4" />
            <span className="text-xs font-bold uppercase tracking-wider">Guided demo · {state.step + 1}/{STEPS.length}</span>
            <button onClick={stop} className="ml-auto rounded p-1 hover:bg-white/15" aria-label="Exit guided demo"><X className="h-4 w-4" /></button>
          </div>
          <motion.div key={state.step} initial={{ x: 16 }} animate={{ x: 0 }} className="space-y-2.5 px-4 py-3">
            <div className="text-base font-bold text-ink">{st.title}</div>
            <div className="rounded-lg bg-tealsoft px-3 py-2 text-xs text-ink2"><b className="text-brand">Show: </b>{st.show}</div>
            <div className="flex gap-2 rounded-lg bg-[#fff1d9] px-3 py-2 text-xs text-ink"><Mic className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" /><span><b>Say: </b>{st.say}</span></div>
            {state.caseId && !loc.pathname.includes(state.caseId) && !['/benchmarks', '/calibration'].includes(loc.pathname) && (
              <button onClick={() => go(state.step)} className="text-[11px] font-semibold text-brand hover:underline">Return to this step's page</button>
            )}
          </motion.div>
          <div className="flex items-center gap-2 border-t border-line px-4 py-2.5">
            <button onClick={() => go(state.step - 1)} disabled={state.step === 0}
              className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold text-ink2 hover:bg-tealsoft disabled:opacity-40"><ChevronLeft className="h-3.5 w-3.5" />Back</button>
            <div className="mx-auto flex gap-1">{STEPS.map((_, i) => (
              <button key={i} onClick={() => go(i)} aria-label={`Step ${i + 1}`}
                className={`h-1.5 rounded-full transition-all ${i === state.step ? 'w-5 bg-brand' : 'w-1.5 bg-line2 hover:bg-teal'}`} />))}</div>
            {state.step < STEPS.length - 1
              ? <button onClick={() => go(state.step + 1)} className="inline-flex items-center gap-1 rounded-full bg-brand px-3 py-1 text-xs font-semibold text-white hover:bg-brand-deep">Next<ChevronRight className="h-3.5 w-3.5" /></button>
              : <button onClick={stop} className="rounded-full bg-accent px-3 py-1 text-xs font-bold text-brand-deep">Finish</button>}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
