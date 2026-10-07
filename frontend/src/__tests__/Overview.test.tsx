/**
 * Overview snapshot + behaviour tests for the two input kinds, using real API responses
 * captured from the backend (src/__tests__/fixtures/*.json).
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CaseDetail } from '../api'
import diskCase from './fixtures/disk.case.json'
import diskMap from './fixtures/disk.diskmap.json'
import diskFile from './fixtures/disk.file.json'
import diskFiles from './fixtures/disk.files.json'
import singleCase from './fixtures/single.case.json'
import singleMap from './fixtures/single.diskmap.json'
import singleFile from './fixtures/single.file.json'
import singleFiles from './fixtures/single.files.json'
import diskInsights from './fixtures/disk.insights.json'
import singleInsights from './fixtures/single.insights.json'

const fx = {
  single: { c: singleCase, files: singleFiles, map: singleMap, file: singleFile, insights: singleInsights },
  disk: { c: diskCase, files: diskFiles, map: diskMap, file: diskFile, insights: diskInsights },
}
let current: keyof typeof fx = 'single'

vi.mock('../api', async (orig) => {
  const mod = await orig<typeof import('../api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      files: vi.fn(async () => fx[current].files),
      diskmap: vi.fn(async () => fx[current].map),
      file: vi.fn(async () => fx[current].file),
    },
  }
})

vi.mock('../api-advanced', async (orig) => {
  const mod = await orig<typeof import('../api-advanced')>()
  return { ...mod, adv: { ...mod.adv, insights: vi.fn(async () => fx[current].insights) } }
})

const { default: Dashboard, intTicks } = await import('../pages/Dashboard')

function renderOverview(kind: keyof typeof fx) {
  current = kind
  const c = fx[kind].c as unknown as CaseDetail
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<Outlet context={{ c, reload: () => {} }} />}>
          <Route path="/" element={<Dashboard />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => { document.body.innerHTML = '' })

describe('Overview – single uploaded PDF (corrupted_demo_report.pdf)', () => {
  it('shows the input type, an explicit status reason and N/A-with-reason metrics', async () => {
    const { container } = renderOverview('single')
    await screen.findByText('corrupted_demo_report__rec0000.pdf', { selector: 'a' })
    expect(screen.getAllByText(/UPLOADED FILE – PDF/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Fragment only: 4\/5 objects intact but no page renders/).length).toBeGreaterThan(0)
    // every metric shows a value or "N/A" plus its reason
    const review = screen.getByText('Review first').closest('section')!
    expect(within(review).getByText('39')).toBeInTheDocument()
    expect(within(review).getByText('100%')).toBeInTheDocument()
    expect(within(review).getByText('43%')).toBeInTheDocument()
    expect(screen.getByText(/no file-system metadata \(single-file input\)/)).toBeInTheDocument()
    // few items -> compact summary instead of near-empty charts
    expect(screen.getByText(/too few for distribution charts/)).toBeInTheDocument()
    expect(container.querySelectorAll('.recharts-wrapper').length).toBe(0)
    // no nested interactive elements (the old overlapping-label layout)
    expect(container.querySelectorAll('button button').length).toBe(0)
    await waitFor(() => expect(screen.getByText(/Object inventory: 4\/5 intact/)).toBeInTheDocument())
    // security + export decisions are explicit: no engine -> NOT SCANNED (never CLEAN); invalid PDF -> forensic artifact
    expect(screen.getAllByText('NOT SCANNED').length).toBeGreaterThan(0)
    expect(screen.getByText(/FORENSIC BYTE ARTIFACT – NOT A VERIFIED RECOVERED FILE/)).toBeInTheDocument()
    // export is a direct download link (no Blob + programmatic click that browsers may block)
    const exportLink = screen.getByText(/Export forensic byte artifact/).closest('a')!
    expect(exportLink.getAttribute('href')).toBe('/api/cases/20260925-194408-7212/files/R001/download?variant=export')
    expect(exportLink.hasAttribute('download')).toBe(true)
    expect(screen.getAllByText(/Preview unavailable/i).length).toBeGreaterThan(0)
    expect(container.querySelector('main, div')!.textContent).toMatchSnapshot()
  })

  it('never shows a warning for a zero count and pluralises correctly', async () => {
    renderOverview('single')
    await screen.findByText('Key findings')
    expect(screen.queryByText(/^0 unallocated/)).toBeNull()
    expect(screen.getByText('1 content family')).toBeInTheDocument()
    expect(screen.getByText('4 sectors · 1 cluster')).toBeInTheDocument()
  })
})

describe('Overview – disk image (FAT12 with a deleted file)', () => {
  it('labels the disk image and shows success lines, not warnings', async () => {
    const { container } = renderOverview('disk')
    await screen.findByText('deleted_notes__rec0000.jpg', { selector: 'a' })
    expect(screen.getAllByText(/DISK IMAGE/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Every byte range that the recovered structures declare was located/)).toBeInTheDocument()
    expect(screen.getByText('1 deleted directory entry')).toBeInTheDocument()
    expect(container.querySelectorAll('svg[aria-label="warning"]').length).toBe(0)
    // wait for the async file preview so the snapshot never captures the transient "Loading…" state
    await screen.findByText(/Export reconstructed file/)
    expect(container.querySelector('main, div')!.textContent).toMatchSnapshot()
  })
})

describe('integer chart ticks', () => {
  it('scales to the data instead of 0-4 for a single item', () => {
    expect(intTicks(1)).toEqual([0, 1])
    expect(intTicks(3)).toEqual([0, 1, 2, 3])
    expect(intTicks(20)).toBeUndefined()
  })
})
