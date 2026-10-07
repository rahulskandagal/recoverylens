import { useEffect, useState } from 'react'
import { Dna, Fingerprint, Search } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import { adv, SIM_META, type DnaCard, type DnaMap, type DnaPage, type SimilarItem } from '../api-advanced'
import type { CaseCtx } from '../components/CaseLayout'
import { Card, DataClass, Empty, ErrorBox, Hint, PageHeader, Spinner, Tag } from '../components/ui'
import { FAMILY_COLOR, bytes } from '../lib/format'

const STATE_TONE = (v: string) => /VALID|PRESENT|COMPLETE/.test(v) ? 'text-emerald-800 bg-emerald-50 ring-emerald-300'
  : /PARTIAL|signature/.test(v) ? 'text-amber-900 bg-amber-50 ring-amber-300' : /DAMAGED/.test(v) ? 'text-red-800 bg-red-50 ring-red-300' : 'text-ink2 bg-track ring-line2'

function Barcode({ profile }: { profile: number[] }) {
  return (
    <div className="flex h-5 overflow-hidden rounded" title="Entropy profile across the fragment (dark = high entropy)">
      {profile.map((e, i) => <div key={i} className="flex-1" style={{ background: `hsl(188 ${40 + e * 5}% ${92 - e * 9}%)` }} />)}
    </div>
  )
}

function MiniCard({ c, active, onClick }: { c: DnaCard; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`rounded-xl border bg-card p-3 text-left transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-lift)] ${active ? 'border-teal ring-2 ring-teal/40' : 'border-line'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-bold text-ink">{c.id}</span>
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: FAMILY_COLOR[c.family] ?? '#8a9aa0' }} title={c.family} />
      </div>
      <div className="mt-0.5 truncate text-[11px] text-muted">{c.family} · {c.offset_hex} · {bytes(c.size)}</div>
      <div className="mt-2"><Barcode profile={c.entropy_profile} /></div>
      <div className="mt-2 flex flex-wrap gap-1 text-[10px]">
        <Tag tone={STATE_TONE(c.header)}>HDR {c.header.split(' ')[0]}</Tag>
        <Tag tone={STATE_TONE(c.footer)}>FTR {c.footer}</Tag>
        <Tag tone="text-ink2 bg-track ring-line2">H {c.entropy.toFixed(2)}</Tag>
      </div>
    </button>
  )
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[130px_1fr] gap-2 border-b border-line py-1.5 text-xs last:border-0"><div className="font-semibold uppercase tracking-wider text-muted">{k}</div><div className="min-w-0 text-ink">{children}</div></div>
}

function DnaDetail({ caseId, fid }: { caseId: string; fid: string }) {
  const [d, setD] = useState<{ card: DnaCard; similar: { engine: string; items: SimilarItem[]; caveat?: string; note?: string } } | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [open, setOpen] = useState<string | null>(null)
  useEffect(() => { setD(null); adv.dna(caseId, fid).then(setD).catch(setErr) }, [caseId, fid])
  if (err) return <ErrorBox error={err} />
  if (!d) return <Spinner />
  const c = d.card
  const hist = c.byte_histogram.map((n, i) => ({ bin: `0x${(i * 8).toString(16).padStart(2, '0').toUpperCase()}`, n }))
  return (
    <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
      <Card icon={Fingerprint} accent="#0f4c5c" title={<span className="flex items-center gap-2">FRAGMENT DNA CARD · <span className="font-mono">{c.id}</span> <DataClass kind="observed" /></span>}
        subtitle={`${c.family} · ${c.offset_hex} · ${bytes(c.size)} · ${c.assigned_to ? `used by ${c.assigned_to}` : 'unassigned'}`}>
        <Barcode profile={c.entropy_profile} />
        <div className="mt-3">
          <Row k="Type / MIME">{c.mime} <span className="text-muted">({c.mime_basis})</span></Row>
          <Row k="Entropy">{c.entropy} bits/byte</Row>
          <Row k="Signature">{c.signature ?? <span className="text-muted">none at fragment start</span>}</Row>
          <Row k="Header"><Tag tone={STATE_TONE(c.header)}>{c.header}</Tag></Row>
          <Row k="Footer"><Tag tone={STATE_TONE(c.footer)}>{c.footer}</Tag> {c.footer_marker && <span className="text-muted">{c.footer_marker}</span>}</Row>
          <Row k="Structure"><Tag tone={STATE_TONE(c.structure)}>{c.structure}</Tag> {c.structural_markers.join(' · ')}</Row>
          <Row k="Compression">{c.compression.detected ? <><b>DETECTED</b> – {c.compression.evidence.join('; ')}</> : 'not detected'}</Row>
          <Row k="Encoding">{c.encoding.label} (printable {(c.encoding.printable * 100).toFixed(0)}%)</Row>
          <Row k="Alignment">sector {c.alignment.sector} {c.alignment.sector_aligned ? '(sector-aligned)' : ''} · cluster {c.alignment.cluster} {c.alignment.cluster_aligned ? '(cluster-aligned)' : ''}</Row>
          <Row k="Parser compat.">{c.parser_compatibility.map(p => <div key={p.parser}><b>{p.parser}</b>: {p.result} <span className="text-muted">– {p.evidence}</span></div>)}</Row>
          <Row k="Metadata">{c.metadata_indicators.length ? c.metadata_indicators.join(' · ') : <span className="text-muted">none found</span>}</Row>
          <Row k="Anomalies">{c.anomalies.length ? <ul className="list-disc pl-4 text-amber-900">{c.anomalies.map(a => <li key={a}>{a}</li>)}</ul> : <span className="text-muted">none detected</span>}</Row>
          <Row k="SHA-256"><span className="break-all font-mono text-[11px]">{c.sha256}</span></Row>
          <Row k="Relationships">{c.potential_relationships} recorded relationship edge(s)</Row>
        </div>
        {c.sample_note && <p className="mt-2 text-[11px] text-muted">{c.sample_note}</p>}
        <div className="mt-3 h-36">
          <ResponsiveContainer>
            <BarChart data={hist} margin={{ left: -28, right: 4, top: 4 }}>
              <CartesianGrid stroke="#eee6d6" vertical={false} />
              <XAxis dataKey="bin" tick={{ fontSize: 9, fill: '#6b7c83' }} interval={3} />
              <YAxis tick={{ fontSize: 9, fill: '#6b7c83' }} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} formatter={(v) => [v, 'bytes in bin']} />
              <Bar dataKey="n" fill={FAMILY_COLOR[c.family] ?? '#1fa6a6'} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="text-center text-[10px] text-muted">Byte-frequency distribution (32 bins of 8 byte values)</p>
      </Card>
      <Card icon={Dna} accent="#7a5bc7" title="Fragment similarity engine" subtitle={d.similar.engine}>
        <Hint>{d.similar.caveat ?? d.similar.note}</Hint>
        <ul className="mt-3 space-y-2">
          {d.similar.items.map(s => (
            <li key={s.id} className="rounded-xl border border-line bg-soft p-2.5">
              <button className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === s.id ? null : s.id)}>
                <span className="font-mono text-sm font-semibold text-ink">{s.id}</span>
                <Tag tone={SIM_META[s.label]}>{s.label}</Tag>
                <span className="text-[11px] text-muted">{s.family} · {s.offset_hex} · cos {s.cosine.toFixed(3)}</span>
                {s.assigned_to && <span className="text-[11px] text-muted">in {s.assigned_to}</span>}
                <span className="ml-auto text-[11px] font-semibold text-brand">{open === s.id ? 'Hide evidence' : 'View evidence'}</span>
              </button>
              {open === s.id && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-ink2">{s.evidence.map(e => <li key={e}>{e}</li>)}</ul>}
            </li>
          ))}
          {!d.similar.items.length && <Empty title="No comparable fragments" />}
        </ul>
      </Card>
    </div>
  )
}

export default function FragmentDNA() {
  const { c } = useOutletContext<CaseCtx>()
  const [sp, setSp] = useSearchParams()
  const [page, setPage] = useState(1)
  const [family, setFamily] = useState('')
  const [q, setQ] = useState('')
  const [data, setData] = useState<DnaPage | null>(null)
  const [map, setMap] = useState<DnaMap | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const sel = sp.get('frag')
  const select = (id: string) => { const n = new URLSearchParams(sp); n.set('frag', id); setSp(n, { replace: true }) }
  useEffect(() => { adv.dnaList(c.id, { page, family, q }).then(setData).catch(setErr) }, [c.id, page, family, q])
  useEffect(() => { adv.dnaMap(c.id).then(setMap).catch(() => setMap(null)) }, [c.id])
  useEffect(() => {
    const first = data?.items.find(i => i.assigned_to) ?? data?.items[0]
    if (!sel && first) select(first.id)
  }, [data]) // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <ErrorBox title="Fragment DNA unavailable" error={err} />
  const fams = map ? [...new Set(map.points.map(p => p.family))] : []
  return (
    <div className="space-y-4">
      <PageHeader icon={Dna} accent="#7a5bc7" title="Fragment DNA"
        subtitle="A deterministic forensic fingerprint for every detected fragment, measured from its own bytes (read-only). Similarity is statistical evidence only and never proves that fragments belong to the same file." />
      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card title="DNA map" subtitle={map ? `${map.note} ${map.engine}.` : 'Loading…'}>
          {map && map.points.length ? (
            <>
              <div className="h-72">
                <ResponsiveContainer>
                  <ScatterChart margin={{ left: -16, right: 8, top: 8 }}>
                    <CartesianGrid stroke="#eee6d6" />
                    <XAxis type="number" dataKey="x" name="PC1" tick={{ fontSize: 10, fill: '#6b7c83' }} />
                    <YAxis type="number" dataKey="y" name="PC2" tick={{ fontSize: 10, fill: '#6b7c83' }} />
                    <ZAxis range={[26, 26]} />
                    <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ fontSize: 11, borderRadius: 8 }}
                      formatter={(v, n) => [typeof v === 'number' ? v.toFixed(3) : v, n]}
                      labelFormatter={() => ''} />
                    <Scatter data={map.points} onClick={p => { const id = (p as unknown as { payload?: { id?: string } }).payload?.id; if (id) select(id) }} cursor="pointer">
                      {map.points.map(p => <Cell key={p.id} fill={FAMILY_COLOR[p.family] ?? '#8a9aa0'} stroke={p.id === sel ? '#0f4c5c' : 'none'} strokeWidth={2} />)}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted">
                {fams.map(f => <span key={f} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: FAMILY_COLOR[f] ?? '#8a9aa0' }} />{f}</span>)}
                {map.explained_variance && <span className="ml-auto">explained variance {map.explained_variance.map(v => `${(v * 100).toFixed(1)}%`).join(' / ')}</span>}
              </div>
            </>
          ) : <Empty title="Projection not available">{map?.note}</Empty>}
        </Card>
        <Card title="Fingerprinted fragments" subtitle={data ? `${data.total} non-empty fragment(s) · ${data.engine}` : undefined}>
          <div className="mb-3 flex flex-wrap gap-2">
            <label className="relative flex-1"><Search className="absolute left-2 top-2 h-4 w-4 text-muted" />
              <input value={q} onChange={e => { setQ(e.target.value); setPage(1) }} placeholder="Fragment or reconstruction ID"
                className="w-full rounded-lg border border-line2 bg-soft py-1.5 pl-8 pr-2 text-sm outline-none focus:border-teal" /></label>
            <select value={family} onChange={e => { setFamily(e.target.value); setPage(1) }} className="rounded-lg border border-line2 bg-soft px-2 text-xs">
              <option value="">All families</option>{data?.families.map(f => <option key={f}>{f}</option>)}
            </select>
          </div>
          {!data ? <Spinner /> : (
            <>
              <div className="grid max-h-[330px] grid-cols-2 gap-2 overflow-y-auto pr-1 scroll-thin sm:grid-cols-3">
                {data.items.map(it => <MiniCard key={it.id} c={it} active={it.id === sel} onClick={() => select(it.id)} />)}
              </div>
              <div className="mt-2 flex items-center justify-between text-xs text-muted">
                <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-full px-3 py-1 hover:bg-tealsoft disabled:opacity-40">← Prev</button>
                <span>page {page} of {Math.max(1, Math.ceil(data.total / data.size))}</span>
                <button disabled={page * data.size >= data.total} onClick={() => setPage(page + 1)} className="rounded-full px-3 py-1 hover:bg-tealsoft disabled:opacity-40">Next →</button>
              </div>
            </>
          )}
        </Card>
      </div>
      {sel && <DnaDetail caseId={c.id} fid={sel} />}
    </div>
  )
}
