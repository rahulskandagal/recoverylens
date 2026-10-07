/** Client + types for Real Recovery Mode: live Windows Recycle Bin evidence, and restore-to-folder. */

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

export interface Drive { drive: string; total_bytes: number | null; free_bytes: number | null; has_recycle_bin: boolean }
export interface RecycleItem {
  scan_id: string; item_id: string; drive: string; sid: string; original_name: string; original_path: string
  deleted_at: string | null; size: number; has_data: boolean; case_id: string | null
}
export interface RecycleScan { id: string; drive: string; created_at: string; item_count: number; recoverable_count: number; errors: string[]; items: RecycleItem[] }
export interface RecycleScanRow { id: string; drive: string; created_at: string; item_count: number; recoverable_count: number }
export interface RestoreResult { destination: string; sha256: string; byte_identical: boolean; export_class: string }
export interface CarveImage { id: string; name: string; mode: string; image_name: string; image_size: number; created_at: string; input_kind: string }
export interface CarveResult {
  status: 'RECOVERED' | 'PARTIALLY_RECOVERABLE' | 'INSUFFICIENT_EVIDENCE'
  case_id: string; file_id: string | null; file_name?: string; recovery_status?: string; reason: string
}

export const realApi = {
  drives: () => j<Drive[]>('/api/real/drives'),
  scan: (drive: string) => post<RecycleScan>('/api/real/recyclebin/scan', { drive }),
  scans: () => j<RecycleScanRow[]>('/api/real/recyclebin/scans'),
  scanDetail: (sid: string) => j<RecycleScan>(`/api/real/recyclebin/scans/${sid}`),
  recover: (sid: string, itemId: string, name?: string) =>
    post<{ case_id: string }>(`/api/real/recyclebin/scans/${sid}/items/${encodeURIComponent(itemId)}/recover`, { name }),
  restore: (caseId: string, fileId: string, destinationPath: string | null, authorized: boolean) =>
    post<RestoreResult>(`/api/cases/${caseId}/files/${fileId}/restore`, { destination_path: destinationPath, authorized }),
  carveImages: () => j<CarveImage[]>('/api/real/recyclebin/carve-images'),
  carve: (sid: string, itemId: string, caseId: string) =>
    post<CarveResult>(`/api/real/recyclebin/scans/${sid}/items/${encodeURIComponent(itemId)}/carve`, { case_id: caseId }),
}
