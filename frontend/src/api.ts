export type Status =
  | 'FULLY_RECOVERED' | 'MOSTLY_RECOVERED' | 'PARTIALLY_RECOVERED'
  | 'FRAGMENT_ONLY' | 'UNCERTAIN' | 'UNRECOVERABLE'
export type Priority = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFORMATIONAL'
export type SecurityStatus = 'CLEAN' | 'SUSPICIOUS' | 'MALWARE_DETECTED' | 'SCAN_FAILED' | 'NOT_SCANNED'
export type ExportClass = 'validated_file' | 'partially_validated_file' | 'forensic_artifact'

export interface SecuritySummary {
  status: SecurityStatus; label: string; scanner: string | null; simulated: boolean; is_test?: boolean; explanation: string
}
export interface Security extends SecuritySummary {
  scanner_version: string | null; sha256: string; detections: string[]; engine_error?: string | null
  yara_matches: { rule: string; description: string; severity: string; offsets: number[] }[]; yara_engine: string
  static: { entropy?: number; max_window_entropy?: number; identified_as?: string
    embedded_executables?: { offset: number; format: string; detail: string }[]
    archive?: { members: number; risky_members: string[]; nested_archives: string[]; encrypted_members: number; macro_project: boolean } | null
    urls?: string[]; indicators?: { severity: string; text: string }[] }
  scan_timestamp: string; executed: false; clean_note?: string | null
}
export interface ExportSummary {
  class: ExportClass; label: string; warning: string | null; byte_identical: boolean
  normal_export_enabled: boolean; authorization_required: boolean
}
export interface ExportInfo extends ExportSummary {
  validation_state: 'validated' | 'partial' | 'failed'; validation_reason: string; file: string
  reconstruction_sha256: string; export_sha256: string | null; exported: boolean; error: string | null; format_valid: boolean
  placeholders: { start: number; end: number; length: number; filled_with: string }[]; observed_bytes: number; observed_sha256: string
}

export interface Dataset { key: string; title: string; summary: string; size_bytes: number; synthetic: boolean; group?: string; featured?: string }

export interface CaseRow {
  id: string; name: string; mode: string; dataset: string | null; status: string; stage: string
  progress: number; created_at: string; finished_at: string | null; image_name: string; image_size: number
}

export interface Summary {
  storage_medium: string; storage_size_bytes: number; total_sectors_scanned: number; total_clusters: number
  potential_fragments_identified: number; candidate_files_identified: number; files_reconstructed: number
  fully_recovered: number; mostly_recovered: number; partially_recovered: number; fragment_only: number
  uncertain: number; unrecoverable: number; unrecoverable_sectors: number; unrecoverable_sectors_definition: string
  overall_recovery_coverage: number | null; key_challenges: string[]; cluster_classes: Record<string, number>
  fragment_families: Record<string, number>; directory_entries: DirEntry[]; analysis_seconds: number; image_sha256: string
  coverage_reason?: string; assigned_coverage?: number | null; assigned_coverage_reason?: string
  input_type?: InputType; findings?: Finding[]
  security?: { engine: string | null; rule_engine: string; simulated_engine: boolean; counts: Record<SecurityStatus, number>
    unassigned_fragments_scanned: number; unassigned_fragments_flagged: number }
}

export interface InputType {
  kind: 'single_file' | 'disk_image' | 'raw_image'; format: string; label: string; evidence: string[]
  filesystem?: string; supported: boolean; note?: string
}
export interface Finding { level: 'warning' | 'info' | 'success'; text: string }
export interface LinksMetric { value: number | null; kind: 'internal_references' | 'fragment_relationships' | 'not_applicable'; label: string; reason: string }
export interface ValidatorCheck {
  id?: string; name: string; status: 'pass' | 'fail' | 'partial' | 'na'; weight?: number; score?: number
  points?: number | null; category: string; detail: string
}
export interface RepairedCopy {
  file: string; sha256: string; carved_sha256: string; size_bytes: number; method: string
  objects_kept: number[]; objects_dropped: number[]; synthesized_objects: string[]
  integrity_score: number; status: Status; status_reason: string; render_pages: number; render_clean: number
  checks: ValidatorCheck[]; note: string
}

export interface DirEntry {
  name: string; short_name: string; deleted: boolean; first_cluster: number; image_cluster: number; size: number
  modified: string | null; name_confidence: number; entry_offset: number; checksum_first_char_recovered: string | null
}

export interface Evaluation {
  disclaimer: string; cluster_placement_precision: number | null; cluster_recall: number; edge_accuracy: number | null
  edges_evaluated: number; wrong_edges: string[]; corruption_bytes_injected: number; corruption_bytes_detected: number
  corruption_note: string
  files: { name: string; type: string; true_surviving_pct: number; clusters: number; surviving_clusters: number
    correctly_recovered_clusters: number; engine_candidate: string | null; engine_name: string | null
    engine_reconstruction_pct: number | null; engine_status: string; header_lost: boolean; bytes_flipped: number }[]
}

export interface ModelCard {
  type: string; trained: boolean; bias: number; limitations: string[]
  features: { name: string; label: string; weight: number }[]
  training: Record<string, number | string>
}

export interface Criteria {
  keywords: string[]; type_weights: Record<string, number>; recency_days: number
  weights: { integrity: number; reconstruction: number; relationship: number; relevance: number }
  bands: Record<string, number>
}

export interface CaseDetail extends CaseRow {
  image_sha256: string; error: string | null; criteria: Criteria; summary: Summary | null
  evaluation: Evaluation | null; model: ModelCard | null; evidence_unchanged: number | null
}

export interface FileRow {
  file_id: string; file_name: string; file_type: string; content_class: string; size_bytes: number
  integrity_score: number; reconstruction_percentage: number | null; relationship_confidence: number
  content_confidence: number; recovery_status: Status; priority_level: Priority; priority_score: number
  priority_reason: string; orphan: boolean; modified: string | null; name_source: string
  fragment_count: number; relationships: number; missing_ranges: number; corrupted_ranges: number; has_preview: boolean
  status_reason?: string; recon_confidence_reason?: string; links_metric?: LinksMetric
  has_repaired_copy?: boolean; input_type?: string
  security?: SecuritySummary; export?: ExportSummary
}

export interface EdgeT {
  src: string; dst: string; type: string; confidence: number; label: string; evidence: string[]
  contributions?: { feature: string; label: string; value: number; contribution: number }[]
  chosen: boolean; features?: Record<string, number>
}

export interface SegmentT {
  start: number; end: number; length: number; kind: 'recovered' | 'missing' | 'corrupted'
  fragment_id: string | null; source_offset: number | null; source_offset_hex: string | null; note: string
}

export interface Check { name: string; status: 'pass' | 'fail' | 'partial' | 'na'; detail: string; category: string }
export interface Factor { factor: string; points: number; max: number; detail: string }

export interface FileDetail extends Omit<FileRow, 'missing_ranges' | 'corrupted_ranges' | 'relationships' | 'security' | 'export'> {
  security?: Security; export?: ExportInfo | null
  export_validation?: { exported: boolean; format_valid: boolean; byte_identical: boolean; sha256: string | null; export_class?: ExportClass; label?: string }
  validator_checks?: ValidatorCheck[]; repaired_copy?: RepairedCopy | null
  reconstruction_sha256?: string; error?: string
  expected_size: number | null; expected_size_source: string
  integrity_factors: Factor[]; reconstruction_basis: string
  relationship_components: Record<string, number | string | null>
  content_confidence_factors: string[]; status_reason: string
  fragments: string[]
  fragment_details: { id: string; offset: number; offset_hex: string; length: number; sector: number; clusters: number; family: string; sha256: string; entropy: number }[]
  fragment_relationships: EdgeT[]
  missing_ranges: SegmentT[]; corrupted_ranges: SegmentT[]; segments: SegmentT[]; conflicts: string[]
  metadata: Record<string, unknown>
  validation: { structural_validation: string; parser_validation: string; checksum_validation: string; checks: Check[]; stats: Record<string, unknown> }
  structure: Record<string, unknown>
  assessment: { exists: string; missing: string; corrupted: string; further_recovery: 'YES' | 'NO' | 'UNCERTAIN'; reason: string; recovered_pct: number | null; missing_pct: number | null }
  preview: null | { kind: 'image'; file: string; unverified: boolean; note: string }
    | { kind: 'pdf'; images: { page: number; file: string }[]; pages: { page: number; status: string; text: string }[]
        render_pages: number; render_clean: number; renderer: string | null; repaired_by_renderer: boolean | null
        inventory: { num: number; offset: number; end: number; intact: boolean; reason: string; type: string | null }[]
        damaged_ranges: [number, number, string][]; text?: string }
    | { kind: 'pages'; pages: { page: number; status: string; text: string }[] }
    | { kind: 'text'; text: string; verified?: boolean }
    | { kind: 'tables'; tables: { name: string; columns: string[]; rows_recovered: number; rows_expected: number | null; rows_expected_source: string; interior_recovered: boolean; sample: Record<string, unknown>[] }[] }
  embedded: { name: string; status: string; node_id: string; size: number }[]
  output_file: string; output_sha256: string; mime: string
  provenance: { source_image: string; source_sha256: string; fragments: { id: string; offset_hex: string; length: number; sha256: string }[]; operations: string[]; analysis_version: string; model: string; timestamp: string; output_note: string }
  priority_breakdown: { component: string; value: number; weight: number; contribution: number }[]
  relevance_score: number
  relevance_indicators: { signal: string; points: number; max: number; detail: string }[]
  explanation: Record<string, string | string[]>
}

export interface Fragment {
  id: string; offset: number; offset_hex: string; length: number; start_cluster: number; clusters: number
  sector: number; family: string; classes: string[]; entropy: number; sha256: string
  header: Record<string, unknown> | null; duplicate_of: string | null; has_footer: boolean; rst_markers: number
  pdf_objects: number[]; zip_members: string[]; ts_first: string | null; ts_last: string | null
  assigned_to: string | null
}

export interface GraphNode {
  id: string; kind: 'file' | 'fragment' | 'metadata' | 'embedded'; label: string; type: string
  status?: Status; integrity?: number; priority?: Priority; group: string | null; offset?: string; length?: number
  header?: boolean; detail?: DirEntry
}
export interface Graph { nodes: GraphNode[]; edges: EdgeT[] }
export interface DiskMap {
  clusters: number; unit?: number; size?: number; cls: number[]; owner: number[]; damaged?: number[]
  legend: Record<string, number>; files: string[]; file_names?: Record<string, string>
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try { const b = await r.json(); msg = b.detail ?? msg } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  config: () => j<{ public_demo: boolean }>('/api/config'),
  datasets: () => j<Dataset[]>('/api/datasets'),
  model: () => j<ModelCard>('/api/model'),
  cases: () => j<CaseRow[]>('/api/cases'),
  case: (id: string) => j<CaseDetail>(`/api/cases/${id}`),
  createDemo: (dataset: string, criteria?: Partial<Criteria>) =>
    j<{ id: string }>('/api/cases', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset, criteria }) }),
  upload: (file: File, authorized: boolean) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('authorized', String(authorized))
    return j<{ id: string }>('/api/cases/upload', { method: 'POST', body: fd })
  },
  deleteCase: (id: string) => j<{ deleted: boolean }>(`/api/cases/${id}`, { method: 'DELETE' }),
  audit: (id: string, after = 0) => j<{ id: number; ts: string; stage: string; message: string }[]>(`/api/cases/${id}/audit?after=${after}`),
  files: (id: string, params: Record<string, string | number> = {}) =>
    j<FileRow[]>(`/api/cases/${id}/files?` + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))),
  file: (id: string, fid: string) => j<FileDetail>(`/api/cases/${id}/files/${fid}`),
  fragments: (id: string, params: Record<string, string | number>) =>
    j<{ total: number; page: number; size: number; items: Fragment[]; families: { family: string; n: number }[] }>(
      `/api/cases/${id}/fragments?` + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))),
  hex: (id: string, frag: string, offset = 0) =>
    j<{ fragment: string; start: number; lines: { offset: string; hex: string; ascii: string }[] }>(`/api/cases/${id}/fragments/${frag}/hex?offset=${offset}&length=512`),
  graph: (id: string) => j<Graph>(`/api/cases/${id}/graph`),
  diskmap: (id: string) => j<DiskMap>(`/api/cases/${id}/diskmap`),
  putCriteria: (id: string, c: Criteria) =>
    j<{ criteria: Criteria; files: FileRow[] }>(`/api/cases/${id}/criteria`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c) }),
  report: (id: string) => j<Record<string, unknown>>(`/api/cases/${id}/report`),
  /** Download export bytes as an ArrayBuffer (binary-safe, never decoded as text). */
  exportBytes: async (id: string, fid: string, variant = 'export', authorized = false) => {
    const r = await fetch(`/api/cases/${id}/files/${fid}/download?variant=${variant}${authorized ? '&authorized=true' : ''}`)
    if (!r.ok) {
      let reason = `${r.status} ${r.statusText}`
      try { const b = await r.json(); reason = b.reason ?? b.detail ?? reason } catch { /* not json */ }
      throw new Error(reason)
    }
    const cd = r.headers.get('content-disposition') ?? ''
    const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? `${fid}.bin`
    return { buf: await r.arrayBuffer(), name, type: r.headers.get('content-type') ?? 'application/octet-stream',
      serverSha: r.headers.get('x-recoverylens-sha256'), expectedSha: r.headers.get('x-recoverylens-expected-sha256') }
  },
  verifyExport: (id: string, fid: string, variant: string, sha256: string, size: number) =>
    j<{ byte_identical: boolean; expected_sha256: string; received_sha256: string }>(`/api/cases/${id}/files/${fid}/export-verification`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ variant, sha256, size }) }),
}
