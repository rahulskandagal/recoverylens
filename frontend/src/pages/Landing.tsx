import { motion } from 'framer-motion'
import { ArrowRight, Binary, CheckCircle2, Eye, FileCheck2, GitBranch, Layers, ListOrdered, MessageSquareText, Ruler, ScanLine, ShieldAlert, Tags } from 'lucide-react'
import { Link } from 'react-router-dom'
import { TourButton } from '../components/Tour'
import { DataClass } from '../components/ui'

const STEPS = [
  { icon: ScanLine, t: 'Find', d: 'Deterministic cluster scan: signatures, entropy, marker legality, page headers, directory remnants.' },
  { icon: Eye, t: 'Understand', d: 'Parse headers, container indexes and metadata of every fragment.' },
  { icon: GitBranch, t: 'Relate', d: 'An explainable edge model scores which fragments continue each other.' },
  { icon: Layers, t: 'Reconstruct', d: 'Assemble along verified anchors (xref, central directory, b-tree, restart markers).' },
  { icon: FileCheck2, t: 'Validate', d: 'Real parsers: libjpeg, zipfile + CRC-32, sqlite integrity_check, PDF object walk.' },
  { icon: Ruler, t: 'Measure', d: 'Reconstruction %, integrity, relationship and content confidence — kept separate.' },
  { icon: Tags, t: 'Classify', d: 'Technical type, content class and transparent relevance indicators.' },
  { icon: ListOrdered, t: 'Prioritize', d: 'Configurable, itemised priority: what to review first — and why.' },
  { icon: MessageSquareText, t: 'Explain', d: 'Every conclusion traces to recorded evidence. No hidden reasoning, no invented bytes.' },
]

const QUESTIONS = [
  'What was found?', 'What belongs to what?', 'What can be reconstructed?', 'How complete is it?',
  'How valid is it?', 'What should be reviewed first?', 'What can realistically be recovered?',
]

export default function Landing() {
  return (
    <div className="grid-bg">
      <section className="mx-auto max-w-[1200px] px-4 pb-10 pt-16">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
          <div className="inline-flex items-center gap-2 rounded-full border border-teal/30 bg-teal/10 px-3 py-1 text-xs text-brand">
            <Binary className="h-3.5 w-3.5" /> AI-assisted recovery intelligence · hackathon prototype
          </div>
          <h1 className="mt-5 max-w-3xl text-4xl font-semibold leading-tight tracking-tight text-ink md:text-5xl">
            From file recovery to <span className="bg-gradient-to-r from-brand to-teal bg-clip-text text-transparent">recovery intelligence</span>.
          </h1>
          <p className="mt-4 max-w-2xl text-lg leading-relaxed text-muted">
            Traditional tools hand investigators thousands of unnamed, duplicated, half-broken fragments. RecoveryLens works out which
            fragments belong together, rebuilds what verifiably exists, measures how complete and valid it is, and explains every step.
          </p>
          <p className="mt-3 font-mono text-sm text-success">Recover what exists. Infer relationships carefully. Never invent evidence.</p>
          <div className="mt-7 flex flex-wrap gap-3">
            <Link to="/new" className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2.5 font-medium text-white hover:bg-brand-deep">
              Start an analysis <ArrowRight className="h-4 w-4" />
            </Link>
            <TourButton className="rounded-lg bg-accent px-4 py-2.5 text-brand-deep hover:bg-accent-2" />
            <Link to="/cases" className="rounded-lg border border-line2 px-4 py-2.5 text-ink hover:bg-tealsoft">Open existing cases</Link>
          </div>
        </motion.div>
      </section>

      <section className="mx-auto max-w-[1200px] px-4 pb-12">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border border-line bg-card p-5">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted">Traditional recovery</div>
            <div className="mt-3 flex items-center gap-2 font-mono text-sm text-ink2">Find <ArrowRight className="h-3.5 w-3.5 text-muted/70" /> Recover</div>
            <p className="mt-3 text-sm text-muted">Output: a folder of <span className="font-mono">f0012384.jpg</span>-style carvings with no idea which are whole, which are mixed, and which matter.</p>
          </div>
          <div className="rounded-xl border border-teal/30 bg-teal/5 p-5">
            <div className="text-xs font-semibold uppercase tracking-wider text-brand">RecoveryLens</div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5 font-mono text-sm text-brand">
              {STEPS.map((s, i) => <span key={s.t} className="flex items-center gap-1.5">{s.t}{i < STEPS.length - 1 && <ArrowRight className="h-3 w-3 text-teal" />}</span>)}
            </div>
            <p className="mt-3 text-sm text-muted">Output: a prioritised, explained recovery report with provenance for every byte.</p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-[1200px] px-4 pb-12">
        <h2 className="text-lg font-semibold text-ink">The pipeline</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((s, i) => (
            <motion.div key={s.t} initial={{ opacity: 0, y: 10 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: i * 0.05 }}
              className="rounded-xl border border-line bg-card p-4">
              <div className="flex items-center gap-2">
                <span className="grid h-7 w-7 place-items-center rounded-md bg-tealsoft font-mono text-xs text-muted">{i + 1}</span>
                <s.icon className="h-4 w-4 text-brand" />
                <span className="font-medium text-ink">{s.t}</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-muted">{s.d}</p>
            </motion.div>
          ))}
        </div>
      </section>

      <section className="mx-auto grid max-w-[1200px] gap-4 px-4 pb-16 md:grid-cols-2">
        <div className="rounded-xl border border-line bg-card p-5">
          <h2 className="font-semibold text-ink">Five kinds of information — never mixed up</h2>
          <ul className="mt-4 space-y-3 text-sm text-muted">
            <li className="flex gap-3"><DataClass kind="observed" /><span>Bytes and offsets read directly from the (write-protected) image.</span></li>
            <li className="flex gap-3"><DataClass kind="derived" /><span>Hashes, entropy and validator results computed from observed data.</span></li>
            <li className="flex gap-3"><DataClass kind="inferred" /><span>Fragment relationships with calibrated confidence (Strong → Uncertain).</span></li>
            <li className="flex gap-3"><DataClass kind="reconstructed" /><span>Files assembled only from observed fragments; gaps listed, zero-filled, never synthesized.</span></li>
            <li className="flex gap-3"><DataClass kind="unverified" /><span>Content whose integrity cannot be checked (no checksum) or previews using borrowed tables.</span></li>
          </ul>
        </div>
        <div className="rounded-xl border border-line bg-card p-5">
          <h2 className="font-semibold text-ink">The seven questions it answers</h2>
          <ol className="mt-4 grid gap-2 text-sm text-ink2">
            {QUESTIONS.map((q, i) => (
              <li key={q} className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-success" /><span className="font-mono text-xs text-muted">{i + 1}.</span>{q}</li>
            ))}
          </ol>
          <div className="mt-5 flex gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-800">
            <ShieldAlert className="h-4 w-4 shrink-0 text-accent" />
            Analyse only storage you are authorised to examine. This prototype works on disk images, never on raw devices, and is not a substitute for professional forensic tooling.
          </div>
        </div>
      </section>
    </div>
  )
}
