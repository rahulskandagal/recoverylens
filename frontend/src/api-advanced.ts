/** Client + types for the advanced analysis services (Fragment DNA, Twin, Possibility, Simulator, …). */

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const b = await r.json(); msg = b.detail ?? b.reason ?? msg } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}
const post = <T,>(url: string, body: unknown = {}) =>
  j<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const qs = (p: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()

export type Level = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | 'N/A'
export type SimLabel = 'STRONG SIMILARITY' | 'POSSIBLE SIMILARITY' | 'WEAK SIMILARITY' | 'NO SIGNIFICANT SIMILARITY'

export interface DnaCard {
  id: string; sha256: string; size: number; offset: number; offset_hex: string; clusters: number; family: string; assigned_to: string | null
  entropy: number; entropy_profile: number[]; byte_histogram: number[]; top_bytes: { byte: string; share: number }[]
  signature: string | null; mime: string; mime_basis: string; header: string; footer: string; footer_marker: string | null; structure: string
  structural_markers: string[]; compression: { detected: boolean; evidence: string[] }
  encoding: { label: string; printable: number; utf8_valid?: boolean }
  alignment: { sector_aligned: boolean; cluster_aligned: boolean; sector: number; cluster: number }
  parser_compatibility: { parser: string; result: string; evidence: string }[]; metadata_indicators: string[]; anomalies: string[]
  potential_relationships: number; sample_note: string | null; data_class: string; signature_engine: string
}
export interface SimilarItem {
  id: string; label: SimLabel; cosine: number; entropy_delta: number; family: string; offset_hex: string
  assigned_to: string | null; same_reconstruction: boolean; evidence: string[]
}
export interface DnaPage { total: number; page: number; size: number; engine: string; ml_engine: string; items: DnaCard[]; families: string[] }
export interface DnaMap { points: { id: string; x: number; y: number; family: string; entropy: number; assigned_to: string | null }[]
  engine: string; explained_variance?: number[]; sampled?: boolean; total?: number; note: string }

export interface StructNode {
  name: string; kind: string; offset: number | null; offset_hex: string | null; length: number; validation: 'pass' | 'partial' | 'fail' | 'na'
  state: 'recovered' | 'missing' | 'corrupted' | 'partial'; source_fragments: string[]; confidence: number; detail: string; children: StructNode[]
}
export interface StructureResult {
  file_id: string; file_name: string; type: string; size: number; available: boolean; status?: string; reason?: string
  parser?: string; tree: StructNode[]; counts?: Record<string, number>; note?: string; registered_parsers: string[]
}

export interface TwinNode { id: string; kind: string; label: string; data_class: string; fragment_id?: string; detail: Record<string, unknown> }
export interface TwinEdge { id: string; src: string; dst: string; kind: string; label: string; detail?: string; confidence?: number | null }
export interface Twin {
  file_id: string; file_name: string; file_type: string; nodes: TwinNode[]; edges: TwinEdge[]
  decisions: { from: string; to: string; text: string; confidence: number | null; evidence: string[]; type: string }[]
  timeline: { ts: string; stage: string; message: string }[]; note: string
}

export interface Candidate { id: string; offset_hex: string; length: number; family: string; assigned_to: string | null
  strength: 'structural' | 'family' | 'duplicate'; evidence: string[]; conflicts: string[]; compatible: boolean }
export interface Region { index: number; kind: 'missing' | 'corrupted'; start: number; end: number; length: number; offset_hex: string
  expected_content: string; known_evidence: string[]; candidates: Candidate[]; level: Level; reason: string }
export interface FilePossibility { file_id: string; file_name: string; file_type: string; status: string; level: Level
  plus: string[]; minus: string[]; regions: Region[]; size: number
  segments: { start: number; end: number; length: number; kind: string; fragment_id: string | null }[]; data_class: string }
export interface Heatmap { unit: number; cells: number; codes: Record<string, number>; state: number[]; owner: (string | null)[]; names: Record<string, string> }
export interface CasePossibility { files: FilePossibility[]; counts: Record<string, number>; heatmap: Heatmap; legend: Record<string, string>; rules: string }

export interface SimEval { integrity: number; reconstruction: number | null; reconstruction_basis: string; status: string; status_reason: string
  validation_state: string; validation_reason: string; checks: Record<string, string>; missing_ranges: number; missing_bytes: number }
export interface SimResult {
  label: string; file_id: string; file_name: string; fragment_id: string
  fragment: { offset_hex: string; length: number; family: string; sha256: string }
  gap: { index: number; start: number; end: number; length: number; bytes_filled: number }
  before: SimEval; after: SimEval
  delta: { integrity: number; integrity_from_coverage: number; integrity_from_validators: number; reconstruction: number | null; missing_bytes: number }
  changed_checks: { check: string; before: string | null; after: string | null; direction: string }[]
  newly_satisfied: string[]; regressed: string[]; newly_resolved_ranges: { start: number; end: number }[]; new_conflicts: string[]
  verdict: string; verdict_reason: string; simulated_sha256: string; evidence_modified: false; persisted: false; timestamp: string; method: string
}

export interface XRel { type: string; type_label: string; source: string; target: string; source_name: string; target_name: string
  confidence: number; label: string; evidence: string[]; data_class: string }
export interface CrossArtifact { relationships: XRel[]; nodes: { id: string; name: string; type: string; status: string; degree: number }[]
  types: Record<string, string>; clusters: string[][]; most_connected: { id: string; name: string; centrality: number }[]; engine: string; caveat: string }

export interface Detection { name: string; scanner: string | null; rule: string | null; evidence: string | null; severity?: string
  region: string | null; timestamp: string | null; scanner_status: string; kind: string; simulated?: boolean }
export interface GuardianItem { file_id: string; file_name: string; file_type: string; status: string; headline: string; raw_status: string
  is_test: boolean; simulated: boolean; explanation: string | null; absence_note: string | null; clean_note: string | null
  detections: Detection[]; scanner: string | null; scanner_version: string | null; engine_status: string; rule_engine: string
  sha256: string | null; scan_timestamp: string | null; isolation: { path: string; sha256: string; read_only: boolean; note: string } | null
  executed: false; opened: false; export_gate: string }
export interface Guardian { items: GuardianItem[]; counts: Record<string, number>; engine: string | null; rule_engine: string
  simulated_engine: boolean; unassigned_scanned: number; unassigned_flagged: number; policy: string[] }

export interface Measure { key: string; name: string; value: number | string | null; unit: string; meaning: string; evidence: string[]; basis: string }
export interface ConfidenceProfile { file_id: string; file_name: string; disclaimer: string; measures: Measure[] }
export interface CalBin { range: string; lo: number; n: number; mean_predicted: number | null; observed_rate: number | null; low_sample: boolean }
export interface CalPart { available: boolean; n: number; status?: string; reason?: string; title?: string; prediction?: string
  positives?: number; brier?: number; ece?: number; precision?: number | null; recall?: number | null
  confusion?: { tp: number; fp: number; fn: number; tn: number }; bins?: CalBin[]; classification_accuracy?: number }
export interface Calibration { disclaimer: string; sources: { kind: string; id: string; dataset: string; edges: number }[]; min_samples: number
  label: string; engine: string; relationship: CalPart; integrity: CalPart }

export interface BenchMetrics { byte_recovery_rate: number | null; byte_recovery_rate_of_surviving: number | null; linking_precision: number | null
  linking_recall: number | null; classification_accuracy: number | null; reconstruction_accuracy: number | null
  integrity_prediction_accuracy: number | null; false_positive_rate: number | null; false_negative_rate: number | null
  junctions: { truth: number; engine: number; correct: number }; noise_clusters: number; surviving_clusters: number; total_clusters: number; correct_clusters: number }
export interface BenchScenario { scenario: string; title: string; damage: string; image_sha256: string
  ground_truth: { name: string; type: string; size: number; sha256: string; surviving_pct: number }[]
  recovered: { name: string; type: string; status: string; integrity: number; reconstruction: number | null; matched_truth: string | null }[]
  metrics: BenchMetrics; file_records: { file: string; integrity: number; predicted_valid: boolean; exact: boolean; type_ok: boolean; status: string }[]
  evaluation: Record<string, number | null> }
export interface BenchRun { id: string; created: string; finished: string | null; status: string; stage: string; progress: number; seed: number
  error?: string | null; data: { label: string; seed?: number; planned: string[]; scenarios: BenchScenario[]; log: { ts: string; message: string }[]
    aggregate?: Record<string, { mean: number | null; n: number }>; note?: string } }
export interface BenchRow { id: string; created: string; finished: string | null; status: string; stage: string; progress: number; seed: number
  scenarios: number; aggregate?: Record<string, { mean: number | null; n: number }> | null }

export interface Rec { id: string; action: string; action_label: string; title: string; target: Record<string, string | number>; reason: string
  evidence: string[]; expected_benefit: string; risk: string; confidence: 'HIGH' | 'MEDIUM' | 'LOW'; confidence_basis: string; priority: number
  planner: string; data_class: string; status: 'pending' | 'executed' | 'rejected' | 'failed'; created: string; decision_ts: string | null
  decision: string | null; result: { summary: string; label: string; detail: Record<string, unknown> } | null }
export interface InvState { recommendations: Rec[]; planner: string; llm: { configured: boolean; model: string | null; status: string; note: string }
  never: string[]; log: { timestamp: string; recommendation: string; evidence: string[]; decision: string; action_taken: string; result: string | null }[] }

export interface Provenance { question: string; artifact: Record<string, unknown>; source_image: { name: string; sha256: string; evidence_unchanged: boolean }
  fragments: { id: string | null; source_offset: string | null; logical: string; length: number; state: string; sha256: string | null }[]
  reconstruction_operations: string[]; validation: Record<string, unknown>; security_scan: Record<string, unknown>
  analysis_version: string; model: string; ai_planner: string; timestamp: string
  user_approved_operations: { id: string; action: string; title: string; decided: string; result: string | null }[]; simulations: { ts: string; message: string }[] }

export interface Insights { average_integrity: number | null; average_reconstruction: number | null; reconstruction_na: number
  relationship_count: number; artifact_relationships: number; possibility_counts: Record<string, number>; heatmap: Heatmap
  heatmap_legend: Record<string, string>; integrity_hist: { bin: string; n: number }[]; reconstruction_hist: { bin: string; n: number }[]
  relationship_conf_hist: { bin: string; n: number }[]; security_counts: Record<string, number> }

export const adv = {
  dnaList: (id: string, p: { page?: number; family?: string; q?: string } = {}) => j<DnaPage>(`/api/cases/${id}/dna?${qs(p)}`),
  dnaMap: (id: string) => j<DnaMap>(`/api/cases/${id}/dna/map`),
  dna: (id: string, frag: string) => j<{ card: DnaCard; similar: { fragment: string; engine: string; items: SimilarItem[]; caveat?: string; note?: string } }>(`/api/cases/${id}/dna/${frag}`),
  structure: (id: string, fid: string) => j<StructureResult>(`/api/cases/${id}/files/${fid}/structure`),
  twin: (id: string, fid?: string) => j<Twin>(fid ? `/api/cases/${id}/files/${fid}/twin` : `/api/cases/${id}/twin`),
  possibility: (id: string) => j<CasePossibility>(`/api/cases/${id}/possibility`),
  filePossibility: (id: string, fid: string) => j<FilePossibility>(`/api/cases/${id}/files/${fid}/possibility`),
  simulate: (id: string, fid: string, fragment_id: string, range_index: number) => post<SimResult>(`/api/cases/${id}/files/${fid}/simulate`, { fragment_id, range_index }),
  cross: (id: string) => j<CrossArtifact>(`/api/cases/${id}/cross-artifact`),
  guardian: (id: string) => j<Guardian>(`/api/cases/${id}/guardian`),
  rescan: (id: string, fid?: string) => post<{ rescanned: number; results: { file_id: string; before: string; after: string }[]; engine: string | null }>(
    `/api/cases/${id}/guardian/rescan${fid ? `?file_id=${fid}` : ''}`),
  confidence: (id: string, fid: string) => j<ConfidenceProfile>(`/api/cases/${id}/files/${fid}/confidence`),
  calibration: () => j<Calibration>('/api/calibration'),
  benchScenarios: () => j<{ scenarios: Record<string, { title: string; damage: string }>; stages: string[]; label: string }>('/api/benchmarks/scenarios'),
  benchStart: (scenarios: string[], seed?: number) => post<{ id: string }>('/api/benchmarks', { scenarios, seed }),
  benchList: () => j<BenchRow[]>('/api/benchmarks'),
  bench: (bid: string) => j<BenchRun>(`/api/benchmarks/${bid}`),
  investigator: (id: string) => j<InvState>(`/api/cases/${id}/investigator`),
  plan: (id: string, focus = '') => post<InvState>(`/api/cases/${id}/investigator/plan`, { focus }),
  decide: (id: string, rid: string, decision: 'approve' | 'reject') => post<{ status: string; result?: Rec['result'] }>(`/api/cases/${id}/investigator/${rid}/decision`, { decision }),
  narrative: (id: string) => post<{ configured: boolean; status: string; note: string; text: string | null; label?: string; error?: string; model: string | null }>(`/api/cases/${id}/investigator/narrative`),
  provenance: (id: string, fid: string) => j<Provenance>(`/api/cases/${id}/files/${fid}/provenance`),
  insights: (id: string) => j<Insights>(`/api/cases/${id}/insights`),
}

export const LEVEL_META: Record<string, { tone: string; color: string; text: string }> = {
  HIGH: { tone: 'text-emerald-800 bg-emerald-50 ring-emerald-300', color: '#22a06b', text: 'HIGH' },
  MEDIUM: { tone: 'text-amber-900 bg-amber-50 ring-amber-300', color: '#f4a340', text: 'MEDIUM' },
  LOW: { tone: 'text-red-800 bg-red-50 ring-red-300', color: '#d9534f', text: 'LOW' },
  UNKNOWN: { tone: 'text-ink2 bg-track ring-line2', color: '#8a9aa0', text: 'UNKNOWN' },
  'N/A': { tone: 'text-teal-800 bg-tealsoft ring-teal/40', color: '#1fa6a6', text: 'N/A – COMPLETE' },
}
export const SIM_META: Record<SimLabel, string> = {
  'STRONG SIMILARITY': 'text-emerald-800 bg-emerald-50 ring-emerald-300',
  'POSSIBLE SIMILARITY': 'text-sky-900 bg-sky-50 ring-sky-300',
  'WEAK SIMILARITY': 'text-amber-900 bg-amber-50 ring-amber-300',
  'NO SIGNIFICANT SIMILARITY': 'text-ink2 bg-track ring-line2',
}
export const GUARD_META: Record<string, { tone: string; color: string }> = {
  'NO DETECTION': { tone: 'text-emerald-800 bg-emerald-50 ring-emerald-300', color: '#22a06b' },
  SUSPICIOUS: { tone: 'text-amber-900 bg-amber-50 ring-amber-400', color: '#f4a340' },
  'MALWARE DETECTED': { tone: 'text-white bg-danger ring-danger', color: '#d9534f' },
  'SCAN UNAVAILABLE': { tone: 'text-ink2 bg-track ring-line2', color: '#6b7c83' },
}
