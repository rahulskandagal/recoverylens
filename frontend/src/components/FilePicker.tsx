import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type FileRow } from '../api'
import { niceType } from '../lib/format'

/** Selected artifact lives in ?file= so pages are linkable from the files table and file detail. */
export function useFileParam(caseId: string, prefer?: (files: FileRow[]) => string | undefined) {
  const [sp, setSp] = useSearchParams()
  const [files, setFiles] = useState<FileRow[] | null>(null)
  useEffect(() => { api.files(caseId).then(setFiles) }, [caseId])
  const fid = sp.get('file') ?? (files ? (prefer?.(files) ?? files[0]?.file_id) : undefined)
  const set = (id: string) => { const n = new URLSearchParams(sp); n.set('file', id); setSp(n, { replace: true }) }
  return { files, fid, set }
}

export default function FilePicker({ files, value, onChange, filter }: {
  files: FileRow[]; value?: string; onChange: (id: string) => void; filter?: (f: FileRow) => boolean
}) {
  const list = filter ? files.filter(filter) : files
  return (
    <label className="flex items-center gap-2 text-xs text-muted">
      Artifact
      <select value={value} onChange={e => onChange(e.target.value)}
        className="max-w-[420px] rounded-lg border border-line2 bg-card px-2.5 py-1.5 text-sm text-ink outline-none focus:border-teal">
        {list.map(f => (
          <option key={f.file_id} value={f.file_id}>
            {f.file_id} · {f.file_name} ({niceType(f.file_type)}{f.missing_ranges ? `, ${f.missing_ranges} gap` : ''})
          </option>
        ))}
      </select>
    </label>
  )
}
