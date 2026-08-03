import { type FormEvent, useEffect, useRef, useState } from 'react'
import './App.css'
import { LandingPage } from './components/LandingPage'
import { MissionControl } from './components/MissionControl'
import type {
  AggregateAnalytics,
  ApplicationContextFull,
  Approval,
  Client,
  Evidence,
  EvidenceVerification,
  ExpertBenchmarkAssessment,
  Event,
  ExecutionJob,
  ExecutorFleetHealth,
  LifecycleMetrics,
  OperationalHealth,
  ReplayComparison,
  Run,
  RunAnalytics,
  RunContext,
  Snapshot,
  Step,
  SupplyChainCompleteness,
  SupplyChainRecord,
  WorkflowProgress,
} from './types'

class ApiRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type OptionalData<T> = {
  data?: T
  unavailable?: string
}

function DashboardApp() {
  const [baseUrl, setBaseUrl] = useState(import.meta.env.VITE_SACM_API_URL || '/api')
  const [actor, setActor] = useState(localStorage.getItem('sacm-actor') || 'local-admin')
  const [token, setToken] = useState('')
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<Run>()
  const [steps, setSteps] = useState<Step[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [clients, setClients] = useState<Client[]>([])
  const [context, setContext] = useState<RunContext>()
  const [analytics, setAnalytics] = useState<RunAnalytics>()
  const [projectAnalytics, setProjectAnalytics] = useState<AggregateAnalytics>()
  const [organizationAnalytics, setOrganizationAnalytics] = useState<AggregateAnalytics>()
  const [comparison, setComparison] = useState<ReplayComparison>()
  const [portfolioAnalytics, setPortfolioAnalytics] = useState<RunAnalytics[]>([])
  const [fullApplication, setFullApplication] = useState<ApplicationContextFull>()
  const [operationalHealth, setOperationalHealth] = useState<OperationalHealth>()
  const [executorFleet, setExecutorFleet] = useState<ExecutorFleetHealth>()
  const [executionJobs, setExecutionJobs] = useState<ExecutionJob[]>([])
  const [supplyChainRecords, setSupplyChainRecords] = useState<SupplyChainRecord[]>([])
  const [supplyChainCompleteness, setSupplyChainCompleteness] = useState<SupplyChainCompleteness>()
  const [evidenceManifest, setEvidenceManifest] = useState<Record<string, unknown>>()
  const [evidenceVerification, setEvidenceVerification] = useState<EvidenceVerification>()
  const [lifecycleMetrics, setLifecycleMetrics] = useState<LifecycleMetrics>()
  const [expertBenchmarkAssessment, setExpertBenchmarkAssessment] = useState<ExpertBenchmarkAssessment>()
  const [progress, setProgress] = useState<WorkflowProgress>()
  const [progressError, setProgressError] = useState('')
  const [error, setError] = useState('')
  const [unavailableData, setUnavailableData] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const loadGeneration = useRef(0)
  const clientsLoadGeneration = useRef(0)

  const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const headers = new Headers(init?.headers)
    headers.set('X-SACM-Actor', actor)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (init?.body) headers.set('Content-Type', 'application/json')
    const response = await fetch(`${baseUrl}${path}`, { ...init, headers })
    if (!response.ok) {
      const body = await response.text()
      let message = body
      try {
        const parsed: unknown = JSON.parse(body)
        if (parsed && typeof parsed === 'object' && 'detail' in parsed && typeof parsed.detail === 'string') {
          message = parsed.detail
        }
      } catch {
        // Keep a non-JSON response as the endpoint's diagnostic.
      }
      throw new ApiRequestError(response.status, message || `${response.status} ${response.statusText}`)
    }
    return response.json() as Promise<T>
  }

  const optional = async <T,>(path: string, label: string): Promise<OptionalData<T>> => {
    try {
      return { data: await request<T>(path) }
    } catch (cause) {
      const message = cause instanceof ApiRequestError
        ? `${label}: ${cause.status} ${cause.message}`
        : `${label}: unavailable`
      return { unavailable: message }
    }
  }

  const clearMissionData = () => {
    setSelected(undefined)
    setSteps([])
    setEvents([])
    setApprovals([])
    setEvidence([])
    setSnapshots([])
    setContext(undefined)
    setAnalytics(undefined)
    setProjectAnalytics(undefined)
    setOrganizationAnalytics(undefined)
    setComparison(undefined)
    setFullApplication(undefined)
    setOperationalHealth(undefined)
    setExecutorFleet(undefined)
    setExecutionJobs([])
    setSupplyChainRecords([])
    setSupplyChainCompleteness(undefined)
    setEvidenceManifest(undefined)
    setEvidenceVerification(undefined)
    setLifecycleMetrics(undefined)
    setProgress(undefined)
    setProgressError('')
  }

  const loadRun = async (run: Run) => {
    const generation = ++loadGeneration.current
    setLoading(true)
    setError('')
    setUnavailableData([])
    try {
      const [current, contextResult, stepsResult, eventsResult, approvalsResult, evidenceResult, analyticsResult, snapshotsResult, comparisonResult, lifecycleMetricsResult] = await Promise.all([
        request<Run>(`/v1/runs/${run.id}`),
        optional<RunContext>(`/v1/runs/${run.id}/context`, 'Mission context'),
        optional<Step[]>(`/v1/runs/${run.id}/steps`, 'Run steps'),
        optional<Event[]>(`/v1/runs/${run.id}/events`, 'Event timeline'),
        optional<Approval[]>(`/v1/approvals?run_id=${run.id}`, 'Approvals'),
        optional<Evidence[]>(`/v1/runs/${run.id}/evidence`, 'Evidence packs'),
        optional<RunAnalytics>(`/v1/runs/${run.id}/analytics`, 'Outcome analytics'),
        optional<Snapshot[]>(`/v1/runs/${run.id}/snapshots`, 'Snapshots'),
        optional<ReplayComparison>(`/v1/runs/${run.id}/comparison`, 'Replay comparison'),
        optional<LifecycleMetrics>(`/v1/runs/${run.id}/lifecycle-metrics`, 'Lifecycle telemetry'),
      ])
      if (generation !== loadGeneration.current) return
      const nextContext = contextResult.data
      const nextSteps = stepsResult.data || []
      const nextEvents = eventsResult.data || []
      const nextApprovals = approvalsResult.data || []
      const nextEvidence = evidenceResult.data || []
      const nextAnalytics = analyticsResult.data
      const nextSnapshots = snapshotsResult.data || []
      const nextComparison = comparisonResult.data
      const nextLifecycleMetrics = lifecycleMetricsResult.data
      const latestEvidence = [...nextEvidence].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0]
      const organizationId = nextContext?.organization?.id
      const [projectAggregateResult, organizationAggregateResult, applicationResult, healthResult, fleetResult, jobsResult, supplyChainResult, completenessResult, manifestResult] = await Promise.all([
        nextContext?.project ? optional<AggregateAnalytics>(`/v1/analytics/projects/${nextContext.project.id}`, 'Project analytics') : Promise.resolve<OptionalData<AggregateAnalytics>>({}),
        organizationId ? optional<AggregateAnalytics>(`/v1/analytics/organizations/${organizationId}`, 'Organization analytics') : Promise.resolve<OptionalData<AggregateAnalytics>>({}),
        optional<ApplicationContextFull>(`/v1/tasks/${run.task_id}/application-context`, 'Application context'),
        optional<OperationalHealth>(`/v1/operations/health${organizationId ? `?organization_id=${organizationId}` : ''}`, 'Operational health'),
        organizationId ? optional<ExecutorFleetHealth>(`/v1/executors/health?organization_id=${organizationId}`, 'Executor fleet health') : Promise.resolve<OptionalData<ExecutorFleetHealth>>({}),
        organizationId ? optional<ExecutionJob[]>(`/v1/operations/execution/jobs?organization_id=${organizationId}`, 'Execution jobs') : Promise.resolve<OptionalData<ExecutionJob[]>>({}),
        optional<SupplyChainRecord[]>(`/v1/runs/${run.id}/supply-chain/records`, 'Supply-chain records'),
        optional<SupplyChainCompleteness>(`/v1/runs/${run.id}/supply-chain/completeness`, 'Supply-chain completeness'),
        latestEvidence ? optional<Record<string, unknown>>(`/v1/runs/${run.id}/evidence/${latestEvidence.id}/manifest`, 'Evidence manifest') : Promise.resolve<OptionalData<Record<string, unknown>>>({}),
      ])
      if (generation !== loadGeneration.current) return
      setSelected(current)
      setContext(nextContext)
      setSteps(nextSteps)
      setEvents(nextEvents)
      setApprovals(nextApprovals)
      setEvidence(nextEvidence)
      setAnalytics(nextAnalytics)
      setSnapshots(nextSnapshots)
      setComparison(nextComparison)
      setProjectAnalytics(projectAggregateResult.data)
      setOrganizationAnalytics(organizationAggregateResult.data)
      setFullApplication(applicationResult.data)
      setOperationalHealth(healthResult.data)
      setExecutorFleet(fleetResult.data)
      setExecutionJobs((jobsResult.data || []).filter((job) => job.run_id === run.id))
      setSupplyChainRecords(supplyChainResult.data || [])
      setSupplyChainCompleteness(completenessResult.data)
      setEvidenceManifest(manifestResult.data)
      setEvidenceVerification(undefined)
      setLifecycleMetrics(nextLifecycleMetrics)
      setUnavailableData([
        contextResult.unavailable,
        stepsResult.unavailable,
        eventsResult.unavailable,
        approvalsResult.unavailable,
        evidenceResult.unavailable,
        analyticsResult.unavailable,
        snapshotsResult.unavailable,
        comparisonResult.unavailable,
        lifecycleMetricsResult.unavailable,
        projectAggregateResult.unavailable,
        organizationAggregateResult.unavailable,
        applicationResult.unavailable,
        healthResult.unavailable,
        fleetResult.unavailable,
        jobsResult.unavailable,
        supplyChainResult.unavailable,
        completenessResult.unavailable,
        manifestResult.unavailable,
      ].filter((item): item is string => Boolean(item)))
    } catch (cause) {
      if (generation === loadGeneration.current) {
        clearMissionData()
        setError(cause instanceof Error ? cause.message : 'Unable to load mission')
      }
    } finally {
      if (generation === loadGeneration.current) setLoading(false)
    }
  }

  const loadRuns = async () => {
    const generation = ++loadGeneration.current
    setLoading(true)
    setError('')
    setUnavailableData([])
    try {
      const [nextRuns, benchmarkResult] = await Promise.all([
        request<Run[]>('/v1/runs'),
        optional<ExpertBenchmarkAssessment>('/v1/benchmarks/expert-assessment', 'Expert assessment'),
      ])
      if (generation !== loadGeneration.current) return
      setRuns(nextRuns)
      setExpertBenchmarkAssessment(benchmarkResult.data)
      const analyticsResults = await Promise.all(nextRuns.map((run) => optional<RunAnalytics>(`/v1/runs/${run.id}/analytics`, `Outcome analytics for ${run.id}`)))
      if (generation !== loadGeneration.current) return
      setPortfolioAnalytics(analyticsResults.flatMap((item) => item.data ? [item.data] : []))
      const current = selected && nextRuns.find((run) => run.id === selected.id)
      if (current) await loadRun(current)
      else if (nextRuns[0]) await loadRun(nextRuns[0])
      else {
        clearMissionData()
        setUnavailableData([
          benchmarkResult.unavailable,
          ...analyticsResults.map((item) => item.unavailable),
        ].filter((item): item is string => Boolean(item)))
      }
    } catch (cause) {
      if (generation === loadGeneration.current) setError(cause instanceof Error ? cause.message : 'Unable to load missions')
    } finally {
      if (generation === loadGeneration.current) setLoading(false)
    }
  }

  const loadClients = async () => {
    const generation = ++clientsLoadGeneration.current
    try {
      const organizations = await request<Omit<Client, 'projects'>[]>('/v1/organizations')
      const populated = await Promise.all(organizations.map(async (organization) => {
        const projects = await optional<Client['projects']>(`/v1/organizations/${organization.id}/projects`, `Projects for ${organization.name}`)
        return { ...organization, projects: projects.data || [], unavailable: projects.unavailable }
      }))
      if (generation !== clientsLoadGeneration.current) return
      setClients(populated.map(({ unavailable: _, ...organization }) => organization))
      const unavailable = populated.flatMap((organization) => organization.unavailable ? [organization.unavailable] : [])
      if (unavailable.length) setUnavailableData((current) => [...current, ...unavailable])
    } catch (cause) {
      if (generation === clientsLoadGeneration.current) {
        setClients([])
        setError(cause instanceof Error ? cause.message : 'Unable to load organizations')
      }
    }
  }

  // Initial connection load; later refreshes are explicit to avoid request loops.
  // oxlint-disable react-hooks/exhaustive-deps
  useEffect(() => {
    void Promise.all([loadRuns(), loadClients()]).catch((cause) => setError(cause instanceof Error ? cause.message : 'Unable to load Mission Control data'))
  }, [])
  // oxlint-enable react-hooks/exhaustive-deps

  useEffect(() => {
    const taskId = selected?.task_id
    if (!taskId) {
      setProgress(undefined)
      setProgressError('')
      return
    }
    let cancelled = false
    const poll = async () => {
      try {
        const next = await request<WorkflowProgress>(`/v1/tasks/${taskId}/progress`)
        if (!cancelled) {
          setProgress(next)
          setProgressError('')
        }
      } catch (cause) {
        if (!cancelled) {
          setProgressError(cause instanceof Error ? cause.message : 'Unable to load live progress')
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  // Authentication and endpoint changes must restart polling.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.task_id, baseUrl, actor, token])

  const action = async (path: string, body?: Record<string, unknown>) => {
    if (!selected) return
    try {
      await request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
      await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Action failed')
    }
  }

  const verifyEvidence = async () => {
    if (!selected || !evidence.length) return
    const latestEvidence = [...evidence].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0]
    try {
      setEvidenceVerification(await request<EvidenceVerification>(`/v1/runs/${selected.id}/evidence/${latestEvidence.id}/verify`, { method: 'POST' }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Evidence verification failed')
    }
  }

  const submitSettings = (event: FormEvent) => {
    event.preventDefault()
    localStorage.setItem('sacm-actor', actor)
    void Promise.all([loadRuns(), loadClients()]).catch((cause) => {
      setError(cause instanceof Error ? cause.message : 'Unable to refresh Mission Control data')
    })
  }

  return <MissionControl
    baseUrl={baseUrl}
    setBaseUrl={setBaseUrl}
    actor={actor}
    setActor={setActor}
    token={token}
    setToken={setToken}
    runs={runs}
    selected={selected}
    steps={steps}
    events={events}
    approvals={approvals}
    evidence={evidence}
    snapshots={snapshots}
    clients={clients}
    context={context}
    analytics={analytics}
    projectAnalytics={projectAnalytics}
    organizationAnalytics={organizationAnalytics}
    comparison={comparison}
    portfolioAnalytics={portfolioAnalytics}
    fullApplication={fullApplication}
    operationalHealth={operationalHealth}
    executorFleet={executorFleet}
    executionJobs={executionJobs}
    supplyChainRecords={supplyChainRecords}
    supplyChainCompleteness={supplyChainCompleteness}
    evidenceManifest={evidenceManifest}
    evidenceVerification={evidenceVerification}
    lifecycleMetrics={lifecycleMetrics}
    expertBenchmarkAssessment={expertBenchmarkAssessment}
    progress={progress}
    progressError={progressError}
    error={error}
    unavailableData={unavailableData}
    loading={loading}
    loadRuns={loadRuns}
    loadRun={loadRun}
    action={action}
    verifyEvidence={verifyEvidence}
    submitSettings={submitSettings}
  />
}

function App() {
  const [showConsole, setShowConsole] = useState(window.location.hash === '#console')

  useEffect(() => {
    const handleHashChange = () => setShowConsole(window.location.hash === '#console')
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  if (!showConsole) return <LandingPage />

  return <>
    <a className="console-home-link" href="#" aria-label="Back to SACM home">SACM home</a>
    <DashboardApp />
  </>
}

export default App
