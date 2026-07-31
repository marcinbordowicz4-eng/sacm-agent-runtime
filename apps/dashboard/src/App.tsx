import { type FormEvent, useEffect, useState } from 'react'
import './App.css'
import { MissionControl } from './components/MissionControl'
import type {
  AggregateAnalytics,
  ApplicationContextFull,
  Approval,
  Client,
  Evidence,
  EvidenceVerification,
  Event,
  ExecutionJob,
  ExecutorFleetHealth,
  OperationalHealth,
  ReplayComparison,
  Run,
  RunAnalytics,
  RunContext,
  Snapshot,
  Step,
  SupplyChainCompleteness,
  SupplyChainRecord,
} from './types'

function App() {
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
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const headers = new Headers(init?.headers)
    headers.set('X-SACM-Actor', actor)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (init?.body) headers.set('Content-Type', 'application/json')
    const response = await fetch(`${baseUrl}${path}`, { ...init, headers })
    if (!response.ok) throw new Error((await response.text()) || `${response.status} ${response.statusText}`)
    return response.json() as Promise<T>
  }

  const optional = async <T,>(path: string): Promise<T | undefined> => {
    try {
      return await request<T>(path)
    } catch {
      return undefined
    }
  }

  const loadRun = async (run: Run) => {
    setLoading(true)
    setError('')
    try {
      const [current, nextContext, nextSteps, nextEvents, nextApprovals, nextEvidence, nextAnalytics, nextSnapshots, nextComparison] = await Promise.all([
        request<Run>(`/v1/runs/${run.id}`),
        request<RunContext>(`/v1/runs/${run.id}/context`),
        request<Step[]>(`/v1/runs/${run.id}/steps`),
        request<Event[]>(`/v1/runs/${run.id}/events`),
        request<Approval[]>(`/v1/approvals?run_id=${run.id}`),
        request<Evidence[]>(`/v1/runs/${run.id}/evidence`),
        request<RunAnalytics>(`/v1/runs/${run.id}/analytics`),
        request<Snapshot[]>(`/v1/runs/${run.id}/snapshots`),
        optional<ReplayComparison>(`/v1/runs/${run.id}/comparison`),
      ])
      const latestEvidence = [...nextEvidence].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0]
      const organizationId = nextContext.organization?.id
      const [projectAggregate, organizationAggregate, nextApplication, nextHealth, nextFleet, nextJobs, nextSupplyChain, nextCompleteness, nextManifest] = await Promise.all([
        nextContext.project ? optional<AggregateAnalytics>(`/v1/analytics/projects/${nextContext.project.id}`) : undefined,
        organizationId ? optional<AggregateAnalytics>(`/v1/analytics/organizations/${organizationId}`) : undefined,
        optional<ApplicationContextFull>(`/v1/tasks/${run.task_id}/application-context`),
        optional<OperationalHealth>(`/v1/operations/health${organizationId ? `?organization_id=${organizationId}` : ''}`),
        organizationId ? optional<ExecutorFleetHealth>(`/v1/executors/health?organization_id=${organizationId}`) : undefined,
        organizationId ? optional<ExecutionJob[]>(`/v1/operations/execution/jobs?organization_id=${organizationId}`) : undefined,
        optional<SupplyChainRecord[]>(`/v1/runs/${run.id}/supply-chain/records`),
        optional<SupplyChainCompleteness>(`/v1/runs/${run.id}/supply-chain/completeness`),
        latestEvidence ? optional<Record<string, unknown>>(`/v1/runs/${run.id}/evidence/${latestEvidence.id}/manifest`) : undefined,
      ])
      setSelected(current)
      setContext(nextContext)
      setSteps(nextSteps)
      setEvents(nextEvents)
      setApprovals(nextApprovals)
      setEvidence(nextEvidence)
      setAnalytics(nextAnalytics)
      setSnapshots(nextSnapshots)
      setComparison(nextComparison)
      setProjectAnalytics(projectAggregate)
      setOrganizationAnalytics(organizationAggregate)
      setFullApplication(nextApplication)
      setOperationalHealth(nextHealth)
      setExecutorFleet(nextFleet)
      setExecutionJobs((nextJobs || []).filter((job) => job.run_id === run.id))
      setSupplyChainRecords(nextSupplyChain || [])
      setSupplyChainCompleteness(nextCompleteness)
      setEvidenceManifest(nextManifest)
      setEvidenceVerification(undefined)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load mission')
    } finally {
      setLoading(false)
    }
  }

  const loadRuns = async () => {
    setLoading(true)
    setError('')
    try {
      const nextRuns = await request<Run[]>('/v1/runs')
      setRuns(nextRuns)
      const analyticsResults = await Promise.all(nextRuns.map((run) => optional<RunAnalytics>(`/v1/runs/${run.id}/analytics`)))
      setPortfolioAnalytics(analyticsResults.filter((item): item is RunAnalytics => item !== undefined))
      const current = selected && nextRuns.find((run) => run.id === selected.id)
      if (current) await loadRun(current)
      else if (nextRuns[0]) await loadRun(nextRuns[0])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load missions')
    } finally {
      setLoading(false)
    }
  }

  const loadClients = async () => {
    const organizations = await request<Omit<Client, 'projects'>[]>('/v1/organizations')
    const populated = await Promise.all(organizations.map(async (organization) => ({
      ...organization,
      projects: await request<Client['projects']>(`/v1/organizations/${organization.id}/projects`),
    })))
    setClients(populated)
  }

  // Initial connection load; later refreshes are explicit to avoid request loops.
  // oxlint-disable react-hooks/exhaustive-deps
  useEffect(() => {
    void Promise.all([loadRuns(), loadClients()]).catch((cause) => setError(cause instanceof Error ? cause.message : 'Unable to load Mission Control data'))
  }, [])
  // oxlint-enable react-hooks/exhaustive-deps

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
    void Promise.all([loadRuns(), loadClients()])
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
    error={error}
    loading={loading}
    loadRuns={loadRuns}
    loadRun={loadRun}
    action={action}
    verifyEvidence={verifyEvidence}
    submitSettings={submitSettings}
  />
}

export default App
