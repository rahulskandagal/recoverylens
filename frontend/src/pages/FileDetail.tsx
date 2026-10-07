import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  ArrowLeft, Brain, CheckSquare, Download, Eye, FileWarning, Fingerprint, History, Map as MapIcon, Scale, SearchCheck,
  ShieldCheck, TriangleAlert, type LucideIcon,
} from 'lucide-react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { api, type FileDetail as FD, type Graph } from '../api'
import { AdvancedLinks, ConfidencePanel, ProvenanceExtras } from '../components/AdvancedFilePanels'
import ByteMap from '../components/ByteMap'
import type { CaseCtx } from '../components/CaseLayout'
import ExportPanel from '../components/ExportPanel'
import FilePreview from '../components/FilePreview'
import RelGraph, { EdgeLegend } from '../components/RelGraph'
import { Card, CheckIcon, DataClass, ErrorBox, Gauge, PriorityBadge, SecurityBadge, Spinner, StatusBadge } from '../components/ui'
import { SECURITY_META, bytes, niceType, pct, relLabel } from '../lib/format'
import { EdgeDetail } from './Graph'

export default function FileDetail() {
  const { c } = useOutletContext<CaseCtx>()
  const { fid = '' } = useParams()
  const [f, setF] = useState<FD | null>(null)
  const [g, setG] = useState<Graph | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [sel, setSel] = useState<string | null>(null)
  useEffect(() => {
    setF(null)
    api.file(c.id, fid).then(setF).catch(setErr)
    api.graph(c.id).then(setG).catch(() => setG({ nodes: [], edges: [] }))
  }, [c.id, fid])
  const sub = useMemo<Graph | null>(() => {
    if (!g || !f) return null
    const ids = new Set<string>([f.file_id, ...f.fragments])
    f.fragment_relationships.forEach(e => { ids.add(e.src); ids.add(e.dst) })
    g.edges.forEach(e => { if (e.src === f.file_id || e.dst === f.file_id) { ids.add(e.src); ids.add(e.dst) } })
    return { nodes: g.nodes.filter(n => ids.has(n.id)), edges: g.edges.filter(e => ids.has(e.src) && ids.has(e.dst)) }
  }, [g, f])
  if (err) return <ErrorBox title="File analysis unavailable" error={err} />
  if (!f) return <Spinner />
  const ex = f.explanation ?? {}
  const a = f.assessment
  const sec = f.security
  const selEdge = sel?.startsWith('edge:') ? f.fragment_relationships.find(e => `edge:${e.src}>${e.dst}` === sel) ?? sub?.edges.find(e => `edge:${e.src}>${e.dst}` === sel) : null
  const internal = f.fragment_relationships.filter(e => e.type !== 'metadata')
  const hex = (n: number) => `0x${n.toString(16).toUpperCase().padStart(8, '0')}`
  return (
    <div className="space-y-5">
      <Link to=".." relative="path" className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-brand"><ArrowLeft className="h-3.5 w-3.5" />All recovered files</Link>

      {f.error && <ErrorBox title="Reconstruction failed" error={f.error} />}

      {/* ---------- summary header ---------- */}
      <section className="overflow-hidden rounded-2xl border border-line bg-card shadow-[var(--shadow-card)]">
        <div className="flex flex-wrap items-start justify-between gap-4 bg-gradient-to-r from-brand to-brand-deep px-6 py-5 text-white">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-widest text-teal-100/80">File name</div>
            <h1 className="mt-0.5 break-all text-2xl font-bold">{f.file_name}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-white/15 px-2.5 py-0.5 font-semibold">{niceType(f.file_type)} · {f.content_class}</span>
              <span className="rounded-full bg-white/15 px-2.5 py-0.5 font-mono">{bytes(f.size_bytes)}</span>
              <span className="rounded-full bg-white/15 px-2.5 py-0.5 font-mono">{f.file_id}</span>
              <span className="text-teal-50/80">name from {f.name_source}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-white p-0.5"><StatusBadge status={f.recovery_status} size="md" /></span>
            <span className="rounded-full bg-white p-0.5"><PriorityBadge level={f.priority_level} /></span>
            <a href="#export" className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-brand-deep hover:bg-accent-2"><Download className="h-4 w-4" />Export</a>
          </div>
        </div>
        <div className="grid gap-5 px-6 py-5 sm:grid-cols-2 xl:grid-cols-5">
          <Gauge label="Integrity" value={f.integrity_score} suffix="/100" sub="structural validity" />
          <Gauge label="Reconstruction" value={f.reconstruction_percentage} sub={f.reconstruction_percentage == null ? 'not estimable (see reason)' : 'expected structure located'} />
          <Gauge label="Relationship confidence" value={f.relationship_confidence} sub={`${relLabel(f.relationship_confidence)}: fragments belong together`} />
          <Gauge label="Content confidence" value={f.content_confidence} sub={`is this really ${niceType(f.file_type)}?`} />
          <div className="flex flex-col justify-center rounded-2xl border border-line bg-soft p-3">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">Security status</div>
            <div className="mt-1"><SecurityBadge status={sec?.status} test={sec?.is_test} size="md" /></div>
            <div className="mt-1 text-[11px] leading-snug text-muted">{sec?.scanner ?? 'no antivirus engine'}{sec?.simulated ? ' (demo)' : ''}</div>
          </div>
        </div>
        <div className="border-t border-line px-6 py-3 text-xs text-ink2"><b className="text-ink">Why this status: </b>{f.status_reason}</div>
      </section>

      <AdvancedLinks fid={f.file_id} firstFragment={f.fragments[0]} />
      <ConfidencePanel caseId={c.id} fid={f.file_id} />

      {f.orphan && <div className="rounded-2xl border border-orange-300 bg-orange-50 p-4 text-sm text-orange-900">
        <b>{f.structure.unstructured ? 'Unstructured data.' : 'Orphan fragments.'}</b> {String(f.structure.reason ?? '')}. Any preview shown is <b>unverified</b>.</div>}

      {/* 1 Recovery evidence */}
      <Section n={1} icon={SearchCheck} accent="#3282b8" title="Recovery evidence" subtitle="Observed source fragments with offsets and hashes" action={<DataClass kind="observed" />}>
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-[10px] font-semibold uppercase tracking-wider text-muted"><tr><th className="py-1.5">Fragment</th><th>Source offset</th><th>Sector</th><th>Length</th><th>Family</th><th>Entropy</th><th>SHA-256</th></tr></thead>
            <tbody className="font-mono">
              {f.fragment_details.map(d => (
                <tr key={d.id} className="border-t border-line text-ink2">
                  <td className="py-1.5"><Link to={`../../fragments?q=${d.id}`} relative="path" className="font-semibold text-brand hover:underline">{d.id}</Link></td>
                  <td>{d.offset_hex}</td><td>{d.sector.toLocaleString()}</td><td>{bytes(d.length)}</td><td>{d.family}</td><td>{d.entropy}</td>
                  <td className="text-muted" title={d.sha256}>{d.sha256.slice(0, 24)}…</td>
                </tr>))}
            </tbody>
          </table>
        </div>
        {f.conflicts.length > 0 && <div className="mt-3 text-xs text-amber-900">Conflicts: {f.conflicts.join('; ')}</div>}
      </Section>

      {/* 2 Reconstruction map */}
      <Section n={2} icon={MapIcon} accent="#1fa6a6" title="Reconstruction map" subtitle="The logical file, byte for byte. Every recovered range points back to its source offset in the evidence image.">
        <ByteMap segments={f.segments} total={f.expected_size ?? f.size_bytes} />
        <p className="mt-3 text-[11px] text-muted">Reconstruction: {f.recon_confidence_reason ?? f.reconstruction_basis} · Expected size: {bytes(f.expected_size)} ({f.expected_size_source})</p>
      </Section>

      <div className="grid gap-5 xl:grid-cols-2">
        {/* 3 Missing ranges */}
        <Section n={3} icon={FileWarning} accent="#d9534f" title="Missing ranges" subtitle="Bytes the file's structure declares but that were not located. They are never filled with invented data.">
          {f.missing_ranges.length ? (
            <ul className="space-y-1.5 font-mono text-xs">{f.missing_ranges.map((m, i) => (
              <li key={i} className="flex justify-between rounded-lg bg-red-50 px-3 py-1.5 text-red-900"><span>Missing: {hex(m.start)} – {hex(m.end - 1)}</span><span>{bytes(m.length)}</span></li>))}</ul>
          ) : <p className="text-sm text-success">No missing ranges: every declared byte was located.</p>}
        </Section>
        {/* 4 Corrupted ranges */}
        <Section n={4} icon={TriangleAlert} accent="#f4a340" title="Corrupted ranges" subtitle="Bytes that are present but fail validation">
          {f.corrupted_ranges.length ? (
            <ul className="space-y-1.5 text-xs">{f.corrupted_ranges.map((m, i) => (
              <li key={i} className="rounded-lg bg-amber-50 px-3 py-1.5 text-amber-950"><span className="font-mono">{hex(m.start)} – {hex(m.end - 1)} @ {m.source_offset_hex}</span><div className="text-[11px]">{m.note}</div></li>))}</ul>
          ) : <p className="text-sm text-ink2">No recovered range fails validation <span className="text-muted">(silent corruption in formats without checksums cannot be ruled out)</span>.</p>}
        </Section>
      </div>

      {/* 5 Validation */}
      <Section n={5} icon={CheckSquare} accent="#22a06b" title="Validation" subtitle={`structure: ${f.validation.structural_validation} · parser: ${f.validation.parser_validation} · checksum/index: ${f.validation.checksum_validation}`}>
        <div className="grid gap-2 md:grid-cols-2">
          {(f.validator_checks?.length ? f.validator_checks : f.validation.checks).map((ch, i) => (
            <div key={i} className="flex gap-2.5 rounded-xl border border-line bg-soft p-3">
              <CheckIcon status={ch.status} />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">{ch.name}
                  {'weight' in ch && typeof ch.weight === 'number' && <span className="rounded-full bg-track px-1.5 text-[10px] font-semibold text-muted">weight {ch.weight}</span>}</div>
                <div className="text-xs text-muted">{ch.detail}</div>
              </div>
            </div>))}
        </div>
        {f.repaired_copy && (
          <div className="mt-3 rounded-xl border border-accent/50 bg-[#fff4e0] p-3 text-xs text-amber-950">
            <b>Repaired copy (derived):</b> {f.repaired_copy.method}. Integrity {f.repaired_copy.integrity_score.toFixed(1)}, {f.repaired_copy.render_clean}/{f.repaired_copy.render_pages} page(s) render.
            {f.repaired_copy.synthesized_objects.length > 0 && <> Structural objects added: {f.repaired_copy.synthesized_objects.join('; ')}.</>}
            <div className="mt-1 font-mono text-[10px]">carved SHA-256 {f.repaired_copy.carved_sha256} · repaired SHA-256 {f.repaired_copy.sha256}</div>
          </div>
        )}
      </Section>

      {/* 6 Security analysis */}
      <Section n={6} icon={ShieldCheck} accent={sec ? SECURITY_META[sec.status].color : '#6b7c83'} title="Security analysis"
        subtitle="Static scan of the recovered bytes as data. The artifact is never executed or opened automatically.">
        {!sec ? <p className="text-sm text-muted">Not scanned.</p> : (
          <div className="space-y-3">
            <div className={`rounded-xl border p-3 text-sm ${sec.status === 'MALWARE_DETECTED' ? 'border-2 border-danger bg-red-50 text-red-900' : sec.status === 'SUSPICIOUS' ? 'border-amber-300 bg-amber-50 text-amber-950' : sec.status === 'CLEAN' ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-line2 bg-soft text-ink2'}`}>
              <div className="flex items-center gap-2"><SecurityBadge status={sec.status} test={sec.is_test} size="md" /></div>
              <p className="mt-2 text-xs leading-relaxed">{sec.explanation}</p>
            </div>
            <div className="grid gap-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
              <KV k="Engine" v={`${sec.scanner ?? 'none available'}${sec.scanner_version ? ` · ${sec.scanner_version}` : ''}`} />
              <KV k="Detections" v={sec.detections.length ? sec.detections.join(', ') : 'none'} />
              <KV k="Rules" v={`${sec.yara_matches.length} matched · ${sec.yara_engine.split(' (')[0]}`} />
              <KV k="Scan time" v={sec.scan_timestamp} />
              <KV k="SHA-256" v={sec.sha256} mono />
              <KV k="Identified as" v={sec.static.identified_as ?? '—'} />
              <KV k="Entropy" v={`${sec.static.entropy ?? '—'} (max window ${sec.static.max_window_entropy ?? '—'})`} />
              <KV k="Executed" v="Never: scanned as data only" />
            </div>
            {sec.yara_matches.length > 0 && (
              <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Matched rules (indicators, not proof)</div>
                <ul className="space-y-1 text-xs">{sec.yara_matches.map(m => (
                  <li key={m.rule} className="rounded-lg bg-soft px-3 py-1.5"><span className="font-mono font-semibold text-ink">{m.rule}</span> <span className="text-muted">({m.severity})</span> · {m.description}
                    {m.offsets.length > 0 && <span className="font-mono text-muted"> @ {m.offsets.map(o => `0x${o.toString(16)}`).join(', ')}</span>}</li>))}</ul></div>
            )}
            {(sec.static.indicators ?? []).length > 0 && (
              <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Static characteristics</div>
                <ul className="space-y-1 text-xs">{sec.static.indicators!.map((i, k) => (
                  <li key={k} className={`rounded-lg px-3 py-1.5 ${i.severity === 'suspicious' ? 'bg-amber-50 text-amber-950' : 'bg-soft text-ink2'}`}>{i.text}</li>))}</ul></div>
            )}
            {sec.clean_note && <p className="text-[11px] text-muted">{sec.clean_note}</p>}
          </div>
        )}
      </Section>

      {/* 7 Provenance */}
      <Section n={7} icon={History} accent="#7a5bc7" title="Provenance" subtitle="Where did this recovered file come from?">
        <div className="grid gap-4 md:grid-cols-[1fr_1.4fr]">
          <dl className="space-y-2 text-xs">
            <KV k="Source image" v={f.provenance.source_image} /><KV k="Source SHA-256" v={f.provenance.source_sha256} mono />
            <KV k="Reconstruction SHA-256" v={f.export?.reconstruction_sha256 ?? f.output_sha256 ?? '—'} mono />
            <KV k="Analysis version" v={`${f.provenance.analysis_version} · ${f.provenance.model}`} />
            <KV k="Timestamp" v={f.provenance.timestamp} />
          </dl>
          <ol className="relative space-y-2 border-l-2 border-teal/30 pl-4 text-xs text-ink2">
            {f.provenance.operations.map((o, i) => <li key={i}><span className="absolute -left-[7px] mt-1 h-3 w-3 rounded-full border-2 border-card bg-teal" />{o}</li>)}
          </ol>
        </div>
        <ProvenanceExtras caseId={c.id} fid={f.file_id} />
      </Section>

      {/* 8 AI / evidence analysis */}
      <Section n={8} icon={Brain} accent="#7a5bc7" title="AI / evidence analysis" subtitle={String(ex.method ?? '')}>
        <div className="grid gap-5 xl:grid-cols-2">
          <div className="space-y-3 text-sm leading-relaxed text-ink2">
            <Expl q="Summary" a={ex.summary} />
            <Expl q="Why were these fragments linked?" a={ex.why_linked} />
            <Expl q={`Why is integrity ${f.integrity_score.toFixed(0)}?`} a={ex.why_integrity} />
            <Expl q={`Why is priority ${f.priority_level}?`} a={ex.why_priority} />
          </div>
          <div>
            {sub && sub.nodes.length > 0 ? <RelGraph graph={sub} height={320} selected={sel} onSelect={setSel} /> : <p className="text-sm text-muted">No relationship graph for this artifact.</p>}
            <div className="mt-2"><EdgeLegend /></div>
            <div className="scroll-thin mt-2 max-h-[220px] overflow-y-auto">
              {selEdge ? <EdgeDetail e={selEdge} /> : (
                <ul className="space-y-1.5 text-xs text-ink2">
                  {((ex.edge_explanations ?? []) as string[]).slice(0, 6).map((t, i) => <li key={i} className="rounded-lg bg-soft p-2 leading-relaxed">{t}</li>)}
                  {internal.length === 0 && <li className="text-muted">No inter-fragment links: single contiguous fragment.</li>}
                </ul>
              )}
            </div>
          </div>
        </div>
      </Section>

      {/* 9 Recovery assessment */}
      <Section n={9} icon={Scale} accent="#f4a340" title="Recovery assessment">
        <div className="grid gap-4 md:grid-cols-[1fr_220px]">
          <dl className="space-y-2.5 text-sm">
            <Row k="What exists">{a.exists}</Row>
            <Row k="What is missing">{a.missing}</Row>
            <Row k="What is corrupted">{a.corrupted}</Row>
            <Row k="Further recovery"><span className={`mr-2 rounded-full px-2 py-0.5 font-mono text-xs font-bold ${a.further_recovery === 'YES' ? 'bg-emerald-100 text-emerald-800' : a.further_recovery === 'NO' ? 'bg-track text-ink2' : 'bg-violet-100 text-violet-800'}`}>{a.further_recovery}</span>{a.reason}</Row>
          </dl>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-1">
            <div className="rounded-xl bg-emerald-50 p-3 text-center"><div className="text-[10px] font-semibold uppercase text-emerald-800">Recovered</div><div className="font-mono text-2xl font-bold text-success">{pct(a.recovered_pct, 1)}</div></div>
            <div className="rounded-xl bg-red-50 p-3 text-center"><div className="text-[10px] font-semibold uppercase text-red-800">Missing</div><div className="font-mono text-2xl font-bold text-danger">{pct(a.missing_pct, 1)}</div></div>
          </div>
        </div>
      </Section>

      {/* 10 Preview + export */}
      <div className="grid gap-5 xl:grid-cols-[1.2fr_1fr]">
        <Section n={10} icon={Eye} accent="#3282b8" title="Preview" subtitle="Rendered only from the same reconstructed bytes that the export serves. Nothing is synthesized.">
          <FilePreview f={f} caseId={c.id} />
        </Section>
        <div id="export">
          <Card icon={Fingerprint} accent="#0f4c5c" title="Export & byte verification"
            subtitle="Validation → security scan → export. The downloaded Blob is re-hashed and compared with the reconstruction SHA-256.">
            <ExportPanel f={f} caseId={c.id} />
          </Card>
        </div>
      </div>
    </div>
  )
}

function Section({ n, icon, accent, title, subtitle, action, children }: {
  n: number; icon: LucideIcon; accent: string; title: string; subtitle?: string; action?: ReactNode; children: ReactNode
}) {
  return (
    <Card icon={icon} accent={accent} subtitle={subtitle} action={action}
      title={<span className="flex items-center gap-2"><span className="grid h-5 w-5 place-items-center rounded-full text-[10px] font-bold text-white" style={{ background: accent }}>{n}</span>{title}</span>}>
      {children}
    </Card>
  )
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return <div><div className="text-[10px] font-semibold uppercase tracking-wider text-muted">{k}</div><div className={`break-all text-ink ${mono ? 'font-mono text-[11px]' : ''}`}>{v}</div></div>
}
function Row({ k, children }: { k: string; children: ReactNode }) {
  return <div><dt className="text-[11px] font-semibold uppercase tracking-wider text-muted">{k}</dt><dd className="text-ink">{children}</dd></div>
}
function Expl({ q, a }: { q: string; a: string | string[] | undefined }) {
  return <div className="rounded-xl bg-soft p-3"><div className="text-xs font-bold text-purple">{q}</div><div className="mt-0.5 text-ink2">{String(a ?? '—')}</div></div>
}
