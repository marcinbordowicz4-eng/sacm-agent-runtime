import { type FormEvent, useEffect, useRef, useState } from 'react'
import './App.css'
import { LandingPage } from './components/LandingPage'
import { MissionControl } from './components/MissionControl'
import type {
  AggregateAnalytics,
  ApplicationContextFull,
  Approval,
  Client,
  ConnectionState,
  CognitiveDeliveryPassport,
  Evidence,
  EvidenceVerification,
  Executor,
  EventLogPage,
  ExpertBenchmarkAssessment,
  Event,
  ExecutionJob,
  ExecutorFleetHealth,
  GovernancePolicy,
  LifecycleMetrics,
  OperationalHealth,
  ReplayComparison,
  Run,
  RunAnalytics,
  RunContext,
  MissionCreateInput,
  OnboardingStatus,
  RepositoryDiff,
  Snapshot,
  Step,
  SupplyChainCompleteness,
  SupplyChainRecord,
  TaskArtifact,
  VerificationMatrix,
  WorkflowProgress,
} from './types'

class ApiRequestError extends Error {
  readonly status: number
  readonly requestId: string

  constructor(status: number, message: string, requestId: string) {
    super(message)
    this.status = status
    this.requestId = requestId
  }
}

type OptionalData<T> = {
  data?: T
  unavailable?: string
}

const savedApiUrl = () => localStorage.getItem('sacm-api-url') || import.meta.env.VITE_SACM_API_URL || '/api'

const newRequestId = () => globalThis.crypto?.randomUUID?.() || `sacm-${Date.now()}-${Math.random().toString(16).slice(2)}`

const connectionForError = (cause: unknown): ConnectionState => {
  if (!(cause instanceof ApiRequestError)) {
    return {
      kind: 'api_unavailable',
      title: 'SACM API cannot be reached',
      detail: 'The browser could not complete the request.',
      next_step: 'Check the API URL, TLS certificate, CORS origin and network route, then retry.',
    }
  }
  if (cause.status === 0) return {
    kind: 'api_unavailable',
    title: 'SACM API cannot be reached',
    detail: cause.message,
    next_step: 'Check the API URL, TLS certificate, CORS origin and network route, then retry.',
    request_id: cause.requestId,
  }
  if (cause.status === 401) return {
    kind: 'authentication_required',
    title: 'Authentication is required',
    detail: cause.message,
    next_step: 'Enter a valid bearer token in Settings, or use an authenticated SACM identity.',
    request_id: cause.requestId,
  }
  if (cause.status === 403) return {
    kind: 'permission_denied',
    title: 'You do not have access to this workspace',
    detail: cause.message,
    next_step: 'Ask an organization owner to grant the required role or tenant permission.',
    request_id: cause.requestId,
  }
  if (cause.status === 404) return {
    kind: 'configuration_required',
    title: 'The configured API path is not a SACM endpoint',
    detail: cause.message,
    next_step: 'Set the API URL to the SACM origin or its reverse-proxy /api path, then reconnect.',
    request_id: cause.requestId,
  }
  return {
    kind: 'api_error',
    title: 'SACM API returned an error',
    detail: cause.message,
    next_step: 'Retry once; if it persists, give the diagnostic ID to the platform operator.',
    request_id: cause.requestId,
  }
}

const connectionMessage = (state: ConnectionState) => `${state.title}: ${state.detail}${state.request_id ? ` (diagnostic ${state.request_id})` : ''}`

function cognitiveDeliveryId(context?: RunContext): string | undefined {
  const candidate = context?.jira_delivery?.context?.cognitive_delivery
  if (!candidate || typeof candidate !== 'object') return undefined
  const value = (candidate as Record<string, unknown>).delivery_id
  return typeof value === 'string' && value ? value : undefined
}

function DashboardApp() {
  const [baseUrl, setBaseUrl] = useState(savedApiUrl)
  const [actor, setActor] = useState(localStorage.getItem('sacm-actor') || 'local-admin')
  const [token, setToken] = useState('')
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<Run>()
  const [steps, setSteps] = useState<Step[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [eventLogNextBefore, setEventLogNextBefore] = useState<number>()
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [artifacts, setArtifacts] = useState<TaskArtifact[]>([])
  const [repositoryDiff, setRepositoryDiff] = useState<RepositoryDiff>()
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [verificationMatrix, setVerificationMatrix] = useState<VerificationMatrix>()
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [clients, setClients] = useState<Client[]>([])
  const [context, setContext] = useState<RunContext>()
  const [analytics, setAnalytics] = useState<RunAnalytics>()
  const [projectAnalytics, setProjectAnalytics] = useState<AggregateAnalytics>()
  const [organizationAnalytics, setOrganizationAnalytics] = useState<AggregateAnalytics>()
  const [governancePolicies, setGovernancePolicies] = useState<GovernancePolicy[]>([])
  const [comparison, setComparison] = useState<ReplayComparison>()
  const [portfolioAnalytics, setPortfolioAnalytics] = useState<RunAnalytics[]>([])
  const [fullApplication, setFullApplication] = useState<ApplicationContextFull>()
  const [operationalHealth, setOperationalHealth] = useState<OperationalHealth>()
  const [executorFleet, setExecutorFleet] = useState<ExecutorFleetHealth>()
  const [executors, setExecutors] = useState<Executor[]>([])
  const [executionJobs, setExecutionJobs] = useState<ExecutionJob[]>([])
  const [organizationJobs, setOrganizationJobs] = useState<ExecutionJob[]>([])
  const [supplyChainRecords, setSupplyChainRecords] = useState<SupplyChainRecord[]>([])
  const [supplyChainCompleteness, setSupplyChainCompleteness] = useState<SupplyChainCompleteness>()
  const [evidenceManifest, setEvidenceManifest] = useState<Record<string, unknown>>()
  const [evidenceVerification, setEvidenceVerification] = useState<EvidenceVerification>()
  const [cognitiveDeliveryPassport, setCognitiveDeliveryPassport] = useState<CognitiveDeliveryPassport>()
  const [lifecycleMetrics, setLifecycleMetrics] = useState<LifecycleMetrics>()
  const [expertBenchmarkAssessment, setExpertBenchmarkAssessment] = useState<ExpertBenchmarkAssessment>()
  const [progress, setProgress] = useState<WorkflowProgress>()
  const [progressError, setProgressError] = useState('')
  const [connection, setConnection] = useState<ConnectionState>({
    kind: 'checking',
    title: 'Checking SACM API',
    detail: 'Connecting to the configured endpoint.',
    next_step: 'Wait for the connection check to complete.',
  })
  const [onboarding, setOnboarding] = useState<OnboardingStatus>({
    organization: 'incomplete',
    repository: 'incomplete',
    executor: 'unknown',
    policy: 'unknown',
    mission: 'incomplete',
  })
  const [error, setError] = useState('')
  const [unavailableData, setUnavailableData] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const loadGeneration = useRef(0)
  const clientsLoadGeneration = useRef(0)

  const request = async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const requestId = newRequestId()
    const configuredBaseUrl = baseUrl.trim().replace(/\/$/, '')
    if (!configuredBaseUrl) throw new ApiRequestError(404, 'API URL is empty.', requestId)
    const headers = new Headers(init?.headers)
    headers.set('X-SACM-Actor', actor)
    headers.set('X-Request-ID', requestId)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    if (init?.body) headers.set('Content-Type', 'application/json')
    let response: Response
    try {
      response = await fetch(`${configuredBaseUrl}${path}`, { ...init, headers })
    } catch {
      throw new ApiRequestError(0, 'Network request failed before SACM returned a response. Verify the endpoint, TLS, CORS and network access.', requestId)
    }
    const responseRequestId = response.headers.get('X-Request-ID') || requestId
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
      throw new ApiRequestError(response.status, message || `${response.status} ${response.statusText}`, responseRequestId)
    }
    return response.json() as Promise<T>
  }

  const optional = async <T,>(path: string, label: string): Promise<OptionalData<T>> => {
    try {
      return { data: await request<T>(path) }
    } catch (cause) {
      const message = cause instanceof ApiRequestError
        ? `${label}: ${cause.status} ${cause.message}${cause.requestId ? ` (diagnostic ${cause.requestId})` : ''}`
        : `${label}: unavailable`
      return { unavailable: message }
    }
  }

  const clearMissionData = () => {
    setSelected(undefined)
    setSteps([])
    setEvents([])
    setEventLogNextBefore(undefined)
    setApprovals([])
    setArtifacts([])
    setRepositoryDiff(undefined)
    setEvidence([])
    setVerificationMatrix(undefined)
    setSnapshots([])
    setContext(undefined)
    setAnalytics(undefined)
    setProjectAnalytics(undefined)
    setOrganizationAnalytics(undefined)
    setGovernancePolicies([])
    setComparison(undefined)
    setFullApplication(undefined)
    setOperationalHealth(undefined)
    setExecutorFleet(undefined)
    setExecutors([])
    setExecutionJobs([])
    setOrganizationJobs([])
    setSupplyChainRecords([])
    setSupplyChainCompleteness(undefined)
    setEvidenceManifest(undefined)
    setEvidenceVerification(undefined)
    setCognitiveDeliveryPassport(undefined)
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
      const [current, contextResult, stepsResult, eventsResult, approvalsResult, artifactsResult, evidenceResult, verificationResult, analyticsResult, snapshotsResult, comparisonResult, lifecycleMetricsResult] = await Promise.all([
        request<Run>(`/v1/runs/${run.id}`),
        optional<RunContext>(`/v1/runs/${run.id}/context`, 'Mission context'),
        optional<Step[]>(`/v1/runs/${run.id}/steps`, 'Run steps'),
        optional<EventLogPage>(`/v1/runs/${run.id}/event-log?limit=200`, 'Event timeline'),
        optional<Approval[]>(`/v1/approvals?run_id=${run.id}`, 'Approvals'),
        optional<TaskArtifact[]>(`/v1/runs/${run.id}/artifacts`, 'Task artifacts'),
        optional<Evidence[]>(`/v1/runs/${run.id}/evidence`, 'Evidence packs'),
        optional<VerificationMatrix>(`/v1/runs/${run.id}/verification`, 'Verification matrix'),
        optional<RunAnalytics>(`/v1/runs/${run.id}/analytics`, 'Outcome analytics'),
        optional<Snapshot[]>(`/v1/runs/${run.id}/snapshots`, 'Snapshots'),
        optional<ReplayComparison>(`/v1/runs/${run.id}/comparison`, 'Replay comparison'),
        optional<LifecycleMetrics>(`/v1/runs/${run.id}/lifecycle-metrics`, 'Lifecycle telemetry'),
      ])
      if (generation !== loadGeneration.current) return
      const nextContext = contextResult.data
      const nextSteps = stepsResult.data || []
      const nextEvents = eventsResult.data?.events || []
      const nextApprovals = approvalsResult.data || []
      const nextArtifacts = artifactsResult.data || []
      const nextEvidence = evidenceResult.data || []
      const nextVerification = verificationResult.data
      const nextAnalytics = analyticsResult.data
      const nextSnapshots = snapshotsResult.data || []
      const nextComparison = comparisonResult.data
      const nextLifecycleMetrics = lifecycleMetricsResult.data
      const latestEvidence = [...nextEvidence].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0]
      const organizationId = nextContext?.organization?.id
      const deliveryId = cognitiveDeliveryId(nextContext)
      const [projectAggregateResult, organizationAggregateResult, governancePoliciesResult, cognitivePassportResult, applicationResult, healthResult, fleetResult, executorsResult, jobsResult, supplyChainResult, completenessResult, manifestResult] = await Promise.all([
        nextContext?.project ? optional<AggregateAnalytics>(`/v1/analytics/projects/${nextContext.project.id}`, 'Project analytics') : Promise.resolve<OptionalData<AggregateAnalytics>>({}),
        organizationId ? optional<AggregateAnalytics>(`/v1/analytics/organizations/${organizationId}`, 'Organization analytics') : Promise.resolve<OptionalData<AggregateAnalytics>>({}),
        organizationId ? optional<GovernancePolicy[]>(`/v1/organizations/${organizationId}/governance/policies${nextContext?.project ? `?project_id=${nextContext.project.id}` : ''}`, 'Governance policies') : Promise.resolve<OptionalData<GovernancePolicy[]>>({}),
        nextContext?.project && deliveryId ? optional<CognitiveDeliveryPassport>(`/v1/projects/${nextContext.project.id}/cognitive/deliveries/${deliveryId}/passport`, 'Cognitive delivery passport') : Promise.resolve<OptionalData<CognitiveDeliveryPassport>>({}),
        optional<ApplicationContextFull>(`/v1/tasks/${run.task_id}/application-context`, 'Application context'),
        optional<OperationalHealth>(`/v1/operations/health${organizationId ? `?organization_id=${organizationId}` : ''}`, 'Operational health'),
        organizationId ? optional<ExecutorFleetHealth>(`/v1/executors/health?organization_id=${organizationId}`, 'Executor fleet health') : Promise.resolve<OptionalData<ExecutorFleetHealth>>({}),
        organizationId ? optional<Executor[]>(`/v1/executors?organization_id=${organizationId}`, 'Executors') : Promise.resolve<OptionalData<Executor[]>>({}),
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
      setEventLogNextBefore(eventsResult.data?.next_before_sequence || undefined)
      setApprovals(nextApprovals)
      setArtifacts(nextArtifacts)
      setRepositoryDiff(undefined)
      setEvidence(nextEvidence)
      setVerificationMatrix(nextVerification)
      setAnalytics(nextAnalytics)
      setSnapshots(nextSnapshots)
      setComparison(nextComparison)
      setProjectAnalytics(projectAggregateResult.data)
      setOrganizationAnalytics(organizationAggregateResult.data)
      setGovernancePolicies(governancePoliciesResult.data || [])
      setCognitiveDeliveryPassport(cognitivePassportResult.data)
      setFullApplication(applicationResult.data)
      setOperationalHealth(healthResult.data)
      setExecutorFleet(fleetResult.data)
      setExecutors(executorsResult.data || [])
      setExecutionJobs((jobsResult.data || []).filter((job) => job.run_id === run.id))
      setOrganizationJobs(jobsResult.data || [])
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
        artifactsResult.unavailable,
        evidenceResult.unavailable,
        verificationResult.unavailable,
        analyticsResult.unavailable,
        snapshotsResult.unavailable,
        comparisonResult.unavailable,
        lifecycleMetricsResult.unavailable,
        projectAggregateResult.unavailable,
        organizationAggregateResult.unavailable,
        governancePoliciesResult.unavailable,
        cognitivePassportResult.unavailable,
        applicationResult.unavailable,
        healthResult.unavailable,
        fleetResult.unavailable,
        executorsResult.unavailable,
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

  const loadOlderEvents = async () => {
    if (!selected || !eventLogNextBefore) return
    setLoading(true)
    setError('')
    try {
      const page = await request<EventLogPage>(
        `/v1/runs/${selected.id}/event-log?limit=200&before_sequence=${eventLogNextBefore}`,
      )
      setEvents((current) => {
        const byId = new Map(current.map((event) => [event.id, event]))
        page.events.forEach((event) => byId.set(event.id, event))
        return [...byId.values()].sort((left, right) => left.sequence - right.sequence)
      })
      setEventLogNextBefore(page.next_before_sequence || undefined)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load earlier events.')
    } finally {
      setLoading(false)
    }
  }

  const loadRuns = async () => {
    const generation = ++loadGeneration.current
    setLoading(true)
    setError('')
    setConnection({
      kind: 'checking',
      title: 'Checking SACM API',
      detail: 'Loading authorized missions.',
      next_step: 'Wait for the connection check to complete.',
    })
    setUnavailableData([])
    try {
      const [nextRuns, benchmarkResult] = await Promise.all([
        request<Run[]>('/v1/runs'),
        optional<ExpertBenchmarkAssessment>('/v1/benchmarks/expert-assessment', 'Expert assessment'),
      ])
      if (generation !== loadGeneration.current) return
      setRuns(nextRuns)
      setConnection(nextRuns.length
        ? {
            kind: 'connected',
            title: 'Connected to SACM API',
            detail: 'Authorized mission data was loaded.',
            next_step: 'Review a mission or create a new one.',
          }
        : {
            kind: 'empty',
            title: 'Connected, with no missions yet',
            detail: 'SACM returned an empty authorized mission set; this is not a zero-value telemetry claim.',
            next_step: 'Complete onboarding, then create the first mission.',
          })
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
      if (generation === loadGeneration.current) {
        const state = connectionForError(cause)
        setConnection(state)
        setError(connectionMessage(state))
      }
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
      const setupChecks = await Promise.all(populated.map(async (organization) => {
        const [executorResult, policyResult] = await Promise.all([
          optional<Executor[]>(`/v1/executors?organization_id=${organization.id}`, `Executors for ${organization.name}`),
          optional<GovernancePolicy[]>(`/v1/organizations/${organization.id}/governance/policies`, `Policies for ${organization.name}`),
        ])
        return { executorResult, policyResult }
      }))
      if (generation !== clientsLoadGeneration.current) return
      const hasRepository = populated.some((organization) => organization.projects.some((project) => Boolean(project.repository_full_name || project.repository_path)))
      const executorUnknown = setupChecks.some((check) => Boolean(check.executorResult.unavailable))
      const policyUnknown = setupChecks.some((check) => Boolean(check.policyResult.unavailable))
      setOnboarding({
        organization: populated.length ? 'complete' : 'incomplete',
        repository: hasRepository ? 'complete' : 'incomplete',
        executor: !populated.length ? 'incomplete' : executorUnknown ? 'unknown' : setupChecks.some((check) => (check.executorResult.data || []).length > 0) ? 'complete' : 'incomplete',
        policy: !populated.length ? 'incomplete' : policyUnknown ? 'unknown' : setupChecks.some((check) => (check.policyResult.data || []).some((policy) => policy.status === 'ACTIVE')) ? 'complete' : 'incomplete',
        mission: runs.length ? 'complete' : 'incomplete',
      })
      const unavailable = [
        ...populated.flatMap((organization) => organization.unavailable ? [organization.unavailable] : []),
        ...setupChecks.flatMap((check) => [check.executorResult.unavailable, check.policyResult.unavailable]),
      ].filter((item): item is string => Boolean(item))
      if (unavailable.length) setUnavailableData((current) => [...current, ...unavailable])
    } catch (cause) {
      if (generation === clientsLoadGeneration.current) {
        setClients([])
        const state = connectionForError(cause)
        setConnection(state)
        setError(connectionMessage(state))
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

  useEffect(() => {
    setOnboarding((current) => ({ ...current, mission: runs.length ? 'complete' : 'incomplete' }))
  }, [runs.length])

  const action = async (path: string, body?: Record<string, unknown>) => {
    if (!selected) return
    try {
      await request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
      await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Action failed')
    }
  }

  const createMission = async (input: MissionCreateInput) => {
    setLoading(true)
    setError('')
    try {
      const run = await request<Run>('/v1/runs', {
        method: 'POST',
        body: JSON.stringify({
          title: input.title,
          description: input.description,
          target_repo_path: input.target_repo_path || null,
          source_revision: input.source_revision || null,
          project_id: input.project_id || null,
        }),
      })
      if (input.startImmediately) await request(`/v1/runs/${run.id}/execute`, { method: 'POST' })
      await loadRuns()
      setOnboarding((current) => ({ ...current, mission: 'complete' }))
      await loadRun(run)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create mission')
      throw cause
    } finally {
      setLoading(false)
    }
  }

  const decideApproval = async (approval: Approval, approve: boolean, reason: string) => {
    if (!selected) return
    try {
      await request(`/v1/approvals/${approval.id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ approve, reason }),
      })
      await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Approval decision failed')
      throw cause
    }
  }

  const captureDiff = async () => {
    if (!selected?.target_repo_path) {
      setError('A repository path is required to capture a diff.')
      return
    }
    try {
      const diff = await request<RepositoryDiff>(`/v1/runs/${selected.id}/diff`, {
        method: 'POST',
      })
      setRepositoryDiff(diff)
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to capture repository diff')
      throw cause
    }
  }

  const buildEvidence = async () => {
    if (!selected) return
    try {
      await request(`/v1/runs/${selected.id}/evidence`, { method: 'POST' })
      await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to build evidence')
      throw cause
    }
  }

  const requeueJob = async (job: ExecutionJob, reason: string) => {
    try {
      await request(`/v1/operations/execution/jobs/${job.id}/requeue`, {
        method: 'POST',
        body: JSON.stringify({ reason, reset_attempts: false }),
      })
      if (selected) await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to requeue execution job')
      throw cause
    }
  }

  const mutatePolicy = async (policy: GovernancePolicy, operation: 'activate' | 'retire') => {
    try {
      await request(`/v1/organizations/${policy.organization_id}/governance/policies/${policy.id}/${operation}`, { method: 'POST' })
      if (selected) await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Unable to ${operation} policy`)
      throw cause
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
    localStorage.setItem('sacm-api-url', baseUrl.trim())
    void Promise.all([loadRuns(), loadClients()]).catch((cause) => {
      const state = connectionForError(cause)
      setConnection(state)
      setError(connectionMessage(state))
    })
  }

  const createOrganization = async (input: { slug: string; name: string }) => {
    await request('/v1/organizations', {
      method: 'POST',
      body: JSON.stringify({ slug: input.slug, name: input.name }),
    })
    await loadClients()
  }

  const createProject = async (input: { organizationId: string; slug: string; name: string; repositoryPath?: string; repositoryFullName?: string }) => {
    await request(`/v1/organizations/${input.organizationId}/projects`, {
      method: 'POST',
      body: JSON.stringify({
        slug: input.slug,
        name: input.name,
        repository_path: input.repositoryPath || null,
        repository_full_name: input.repositoryFullName || null,
      }),
    })
    await loadClients()
  }

  const createBaselinePolicy = async (organizationId: string, region: string) => {
    const categories = ['source_context', 'task_metadata', 'runtime_events', 'logs', 'artifacts', 'evidence', 'backups', 'analytics', 'audit']
    const policy = await request<GovernancePolicy>(`/v1/organizations/${organizationId}/governance/policies`, {
      method: 'POST',
      body: JSON.stringify({
        name: 'Onboarding baseline',
        description: 'Explicitly created baseline for an initial SACM mission. Review and replace this policy before production use.',
        rules: categories.map((resource_category) => ({
          resource_category,
          classification: 'Internal',
          retention_days: 90,
          deletion_mode: 'TOMBSTONE',
          exportable: true,
          allowed_regions: [region],
          storage_classes: ['standard'],
          evidence_preservation: 'PRESERVE',
        })),
      }),
    })
    await request(`/v1/organizations/${organizationId}/governance/policies/${policy.id}/activate`, { method: 'POST' })
    await loadClients()
  }

  const createEnrollmentToken = async (organizationId: string) => {
    const issued = await request<{ enrollment_token: string; expires_at: string }>('/v1/executors/enrollment-tokens', {
      method: 'POST',
      body: JSON.stringify({ organization_id: organizationId, expires_in_seconds: 900 }),
    })
    return issued
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
    eventLogHasMore={Boolean(eventLogNextBefore)}
    approvals={approvals}
    artifacts={artifacts}
    repositoryDiff={repositoryDiff}
    evidence={evidence}
    verificationMatrix={verificationMatrix}
    snapshots={snapshots}
    clients={clients}
    context={context}
    analytics={analytics}
    projectAnalytics={projectAnalytics}
    organizationAnalytics={organizationAnalytics}
    governancePolicies={governancePolicies}
    comparison={comparison}
    portfolioAnalytics={portfolioAnalytics}
    fullApplication={fullApplication}
    operationalHealth={operationalHealth}
    executorFleet={executorFleet}
    executors={executors}
    executionJobs={executionJobs}
    organizationJobs={organizationJobs}
    supplyChainRecords={supplyChainRecords}
    supplyChainCompleteness={supplyChainCompleteness}
    evidenceManifest={evidenceManifest}
    evidenceVerification={evidenceVerification}
    cognitiveDeliveryPassport={cognitiveDeliveryPassport}
    lifecycleMetrics={lifecycleMetrics}
    expertBenchmarkAssessment={expertBenchmarkAssessment}
    progress={progress}
    progressError={progressError}
    connection={connection}
    onboarding={onboarding}
    error={error}
    unavailableData={unavailableData}
    loading={loading}
    loadRuns={loadRuns}
    loadRun={loadRun}
    loadOlderEvents={loadOlderEvents}
    action={action}
    createMission={createMission}
    decideApproval={decideApproval}
    captureDiff={captureDiff}
    buildEvidence={buildEvidence}
    requeueJob={requeueJob}
    activatePolicy={(policy) => mutatePolicy(policy, 'activate')}
    retirePolicy={(policy) => mutatePolicy(policy, 'retire')}
    verifyEvidence={verifyEvidence}
    createOrganization={createOrganization}
    createProject={createProject}
    createBaselinePolicy={createBaselinePolicy}
    createEnrollmentToken={createEnrollmentToken}
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
