import { useEffect, useState } from 'react'
import { Beaker, FolderSearch, Plus, Scale, ScanSearch, ShieldCheck } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { TourButton } from './Tour'

export default function Shell() {
  // Defaults to hidden (not "false"/local) until /api/config resolves, so the Recovery Shield nav
  // link never appears-then-vanishes and shifts the other nav items right as the page loads.
  const [publicDemo, setPublicDemo] = useState(true)
  useEffect(() => { api.config().then(c => setPublicDemo(c.public_demo)).catch(() => setPublicDemo(false)) }, [])
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-full px-3.5 py-1.5 text-sm font-medium transition ${isActive ? 'bg-white/15 text-white' : 'text-teal-50/80 hover:bg-white/10 hover:text-white'}`
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 bg-brand text-white shadow-[0_2px_12px_rgba(15,76,92,0.25)]">
        <div className="mx-auto flex h-15 max-w-[1500px] items-center gap-4 px-4">
          <NavLink to="/" className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-teal shadow-inner">
              <ScanSearch className="h-5 w-5 text-white" />
            </span>
            <span className="text-[17px] font-bold tracking-tight">Recovery<span className="text-accent-2">Lens</span></span>
            <span className="hidden rounded-full bg-white/10 px-2 py-0.5 font-mono text-[10px] text-teal-50/80 sm:inline">forensic prototype</span>
            {publicDemo && <span className="hidden rounded-full bg-accent/90 px-2 py-0.5 font-mono text-[10px] font-bold text-brand-deep sm:inline">public demo</span>}
          </NavLink>
          <nav className="ml-4 flex items-center gap-1">
            <NavLink to="/cases" className={link}><span className="flex items-center gap-1.5"><FolderSearch className="h-4 w-4" />Cases</span></NavLink>
            {!publicDemo && <NavLink to="/shield" className={link}><span className="flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" />Recovery Shield</span></NavLink>}
            <NavLink to="/benchmarks" className={link}><span className="flex items-center gap-1.5"><Beaker className="h-4 w-4" />Benchmark Lab</span></NavLink>
            <NavLink to="/calibration" className={link}><span className="hidden items-center gap-1.5 md:flex"><Scale className="h-4 w-4" />Calibration</span></NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <TourButton className="hidden bg-white/10 text-white ring-1 ring-inset ring-white/25 hover:bg-white/20 sm:inline-flex" />
            <NavLink to="/new" className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-sm font-semibold text-brand-deep shadow hover:bg-accent-2">
              <Plus className="h-4 w-4" />New analysis
            </NavLink>
          </div>
        </div>
      </header>
      <Outlet />
    </div>
  )
}
