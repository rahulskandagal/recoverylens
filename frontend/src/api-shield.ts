/** Client + types for the Recovery Shield module (continuous folder protection, snapshots, incidents, restore). */

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
const del = <T,>(url: string) => j<T>(url, { method: 'DELETE' })

export interface ShieldOverview {
  sources: number; active_protection: number; protected_files: number; recovery_points: number
  incidents: number; deleted_detected: number; modified_detected: number; corrupted_detected: number
  recoverable: number; unrecoverable: number; message: string
}
export interface SnapshotSummary { id: string; created_at: string; status: string; file_count: number; total_size: number; error?: string | null }
export interface Source {
  id: string; name: string; path: string; created_at: string; last_scan_at: string | null; protection: string
  monitor_error: string | null; path_accessible: boolean; snapshot_count: number; latest_snapshot: SnapshotSummary | null
  incident_count: number; monitoring: boolean
}
export interface SnapshotFile { rel_path: string; size: number; mtime: string; sha256: string; stored_path?: string }
export interface Snapshot extends SnapshotSummary { source_id: string; manifest_sha256: string | null; files: SnapshotFile[] }
export interface VerifyResult { snapshot_id: string; status: string; checked: number; mismatches: { rel_path: string; reason: string }[] }

export interface DeletedRow { rel_path: string; size: number; recoverable: boolean; source: string }
export interface ChangeRow { rel_path: string; reason?: string }
export interface Diff {
  source_id: string; scanned_at: string; baseline_snapshot: string; deleted: DeletedRow[]; modified: ChangeRow[]
  corrupted: ChangeRow[]; new_files: { rel_path: string }[]; renamed: { from: string; to: string }[]
  unchanged: number; incident_id: string | null
}
export interface Incident {
  id: string; source_id: string; created_at: string; severity: 'HIGH' | 'MEDIUM' | 'LOW'; summary: string
  recovery_point: string; details: { deleted: DeletedRow[]; modified: ChangeRow[]; corrupted: ChangeRow[]; new_files: { rel_path: string }[]; renamed: { from: string; to: string }[] }
}
export interface RestoreResult {
  id: string; destination: string; destination_mode: string; requested: number
  restored: { rel_path: string; destination: string; byte_identical: boolean }[]
  failed: { rel_path: string; reason: string }[]
}
export interface AuditRow { id: number; ts: string; stage: string; message: string }

export const shieldApi = {
  overview: () => j<ShieldOverview>('/api/shield/overview'),
  sources: () => j<Source[]>('/api/shield/sources'),
  source: (sid: string) => j<Source>(`/api/shield/sources/${sid}`),
  createSource: (name: string, path: string) => post<Source>('/api/shield/sources', { name, path }),
  removeSource: (sid: string) => del<{ deleted: boolean }>(`/api/shield/sources/${sid}?confirm=true`),
  createSnapshot: (sid: string) => post<{ id: string }>(`/api/shield/sources/${sid}/snapshots`),
  snapshots: (sid: string) => j<SnapshotSummary[]>(`/api/shield/sources/${sid}/snapshots`),
  snapshot: (snapId: string) => j<Snapshot>(`/api/shield/snapshots/${snapId}`),
  verify: (snapId: string) => post<VerifyResult>(`/api/shield/snapshots/${snapId}/verify`),
  scan: (sid: string) => post<Diff>(`/api/shield/sources/${sid}/scan`),
  monitor: (sid: string, enable: boolean) => post<{ monitoring: boolean; engine?: string }>(`/api/shield/sources/${sid}/monitor`, { enable }),
  audit: (sid: string, after = 0) => j<AuditRow[]>(`/api/shield/sources/${sid}/audit?after=${after}`),
  incidents: (sourceId?: string) => j<Incident[]>(`/api/shield/incidents${sourceId ? `?source_id=${sourceId}` : ''}`),
  incident: (iid: string) => j<Incident>(`/api/shield/incidents/${iid}`),
  restore: (sid: string, snapshotId: string, files: string[], destinationMode: string, destinationPath: string | null, authorized: boolean) =>
    post<RestoreResult>(`/api/shield/sources/${sid}/restore`,
      { snapshot_id: snapshotId, files, destination_mode: destinationMode, destination_path: destinationPath, authorized }),
  restores: (sid: string) => j<RestoreResult[]>(`/api/shield/sources/${sid}/restores`),
}

export const SEVERITY_TONE: Record<string, string> = {
  HIGH: 'text-white bg-danger ring-danger', MEDIUM: 'text-amber-900 bg-amber-50 ring-amber-400', LOW: 'text-ink2 bg-track ring-line2',
}
export const PROTECTION_TONE: Record<string, string> = {
  active: 'text-emerald-800 bg-emerald-50 ring-emerald-300', paused: 'text-amber-900 bg-amber-50 ring-amber-300',
  inactive: 'text-ink2 bg-track ring-line2', error: 'text-red-800 bg-red-50 ring-red-300',
}
