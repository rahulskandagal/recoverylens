import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import CaseLayout from './components/CaseLayout'
import ErrorBoundary from './components/ErrorBoundary'
import Shell from './components/Shell'
import { TourProvider } from './components/Tour'
import { Spinner } from './components/ui'
import Audit from './pages/Audit'
import Cases from './pages/Cases'
import Dashboard from './pages/Dashboard'
import Files from './pages/Files'
import Landing from './pages/Landing'
import NewAnalysis from './pages/NewAnalysis'
import Priorities from './pages/Priorities'
import Progress from './pages/Progress'

const Evaluation = lazy(() => import('./pages/Evaluation'))
const FileDetail = lazy(() => import('./pages/FileDetail'))
const Fragments = lazy(() => import('./pages/Fragments'))
const GraphPage = lazy(() => import('./pages/Graph'))
const Report = lazy(() => import('./pages/Report'))
const FragmentDNA = lazy(() => import('./pages/FragmentDNA'))
const DigitalTwin = lazy(() => import('./pages/DigitalTwin'))
const StructureExplorer = lazy(() => import('./pages/StructureExplorer'))
const RecoveryPossibility = lazy(() => import('./pages/RecoveryPossibility'))
const SecurityGuardian = lazy(() => import('./pages/SecurityGuardian'))
const CrossArtifact = lazy(() => import('./pages/CrossArtifact'))
const Investigator = lazy(() => import('./pages/Investigator'))
const BenchmarkLab = lazy(() => import('./pages/BenchmarkLab'))
const Calibration = lazy(() => import('./pages/Calibration'))
const ShieldHome = lazy(() => import('./pages/ShieldHome'))
const ShieldSource = lazy(() => import('./pages/ShieldSource'))

export default function App() {
  return (
    <ErrorBoundary>
    <TourProvider>
    <Suspense fallback={<Spinner />}>
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Landing />} />
        <Route path="/new" element={<NewAnalysis />} />
        <Route path="/cases" element={<Cases />} />
        <Route path="/cases/:id/progress" element={<Progress />} />
        <Route path="/benchmarks" element={<BenchmarkLab />} />
        <Route path="/calibration" element={<Calibration />} />
        <Route path="/shield" element={<ShieldHome />} />
        <Route path="/shield/:sid" element={<ShieldSource />} />
        <Route path="/cases/:id" element={<CaseLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="files" element={<Files />} />
          <Route path="files/:fid" element={<FileDetail />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="fragments" element={<Fragments />} />
          <Route path="priorities" element={<Priorities />} />
          <Route path="report" element={<Report />} />
          <Route path="audit" element={<Audit />} />
          <Route path="evaluation" element={<Evaluation />} />
          <Route path="dna" element={<FragmentDNA />} />
          <Route path="twin" element={<DigitalTwin />} />
          <Route path="structure" element={<StructureExplorer />} />
          <Route path="possibility" element={<RecoveryPossibility />} />
          <Route path="guardian" element={<SecurityGuardian />} />
          <Route path="cross" element={<CrossArtifact />} />
          <Route path="investigator" element={<Investigator />} />
        </Route>
        <Route path="*" element={<Navigate to="/" />} />
      </Route>
    </Routes>
    </Suspense>
    </TourProvider>
    </ErrorBoundary>
  )
}
