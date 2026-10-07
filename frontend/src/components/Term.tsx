import { HelpCircle } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

export const GLOSSARY: Record<string, string> = {
  integrity: 'How structurally valid the recovered file is (0–100). Weighted sum of format-specific checks; click the score for the full checklist. It says nothing about completeness.',
  recon: 'Reconstruction: the share of the file’s expected structure that was located. “Contiguous” means the file came as one unbroken block, so nothing had to be reassembled.',
  links: 'For PDFs: the share of indirect object references (N 0 R) that resolve to intact objects. For carved multi-fragment files: confidence that the fragments belong together.',
  priority: 'Suggested review order under the configured criteria (evidence scores + keywords, type, recency). It means “review earlier”, not “proven important”.',
  coverage: 'Bytes located ÷ bytes that recovered structures (or file-system metadata) say should exist. N/A when nothing declares an expected size, e.g. a single uploaded file.',
  evidence: 'The input is copied to a write-protected store and SHA-256-hashed before and after analysis. “Unchanged” means both hashes match: the original was never modified.',
  status: 'Recovery status is decided by named validator checks; the reason lists the ones that decided it.',
  fragments: 'Runs of adjacent 4 KiB clusters with the same content class, found by the deterministic scanner.',
}

export default function Term({ k, children, className = '' }: { k: keyof typeof GLOSSARY | string; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <span className={`relative inline-flex items-center gap-1 ${className}`}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {children}
      <button type="button" aria-describedby={id} aria-label={`What is ${String(children)}?`}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={e => { e.preventDefault(); e.stopPropagation(); setOpen(o => !o) }}
        className="text-muted hover:text-teal focus:text-brand focus:outline-none">
        <HelpCircle className="h-3 w-3" />
      </button>
      {open && (
        <span role="tooltip" id={id}
          className="absolute left-0 top-full z-50 mt-1 w-64 rounded-lg border border-line2 bg-soft p-2.5 text-[11px] font-normal normal-case leading-relaxed tracking-normal text-ink shadow-xl">
          {GLOSSARY[k] ?? k}
        </span>
      )}
    </span>
  )
}
