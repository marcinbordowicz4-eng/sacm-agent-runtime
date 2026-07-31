import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import './App.css'

type Run = {
  id: string
  task_id: string
  project_id?: string | null
  status: string
  workflow_version?: string
  source_revision?: string | null
  target_repo_path?: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
}
type Step = { id: string; sequence: number; name: string; status: string; retry_count: number; started_at?: string | null; completed_at?: string | null }
type Event = { id: string; sequence: number; event_type: string; actor: string; payload: Record<string, unknown>; occurred_at: string }
type Approval = { id: string; action: string; status: string; requested_at: string; decided_at?: string | null; resource: Record<string, unknown> }
type Evidence = { id: string; path: string; manifest_hash: string; created_at: string }
type Snapshot = { id: string; event_sequence: number; checksum: string; creation_reason: string; created_at: string }
type Client = { id: string; slug: string; name: string; projects: { id: string; name: string; repository_full_name?: string | null }[] }
type AgentConfiguration = {
  agent_name?: string
  role?: string
  runtime_kind?: string
  implementation_ref?: string
  configuration?: Record<string, unknown>
}
type PlanStep = {
  id: string
  sequence: number
  kind: string
  title: string
  objective: string
  acceptance_criteria: string[]
  required_tools: string[]
  risk_tags: string[]
  depends_on: string[]
  agent: AgentConfiguration
}
type ExecutionPlan = {
  id: string
  revision: number
  status: string
  policy_pack: string
  risk_decision: Record<string, unknown>
  policy_decision: Record<string, unknown>
  security_review?: {
    required: boolean
    status: string
    reviewer: AgentConfiguration
    findings: Record<string, unknown>[]
    reviewed_at?: string | null
    reviewed_by?: string | null
  } | null
  approval_gates: { id: string; gate_type: string; action: string; reason: string; status: string; step_ids: string[]; approval_id?: string | null }[]
  steps: PlanStep[]
}
type RunContext = {
  run: { id: string; workflow_version: string; source_revision?: string | null; target_repo_path?: string | null }
  task: {
    id: string
    title: string
    description: string
    status: string
    contract_version?: string | null
    connector_type?: string | null
    external_id?: string | null
    external_url?: string | null
    task_contract?: Record<string, unknown> | null
    readiness_score?: number | null
    readiness_details?: Record<string, unknown> | null
    clarifications?: { id: string; field_name: string; question: string; status: string; answer?: unknown; created_at: string; answered_at?: string | null }[]
    created_at: string
    updated_at: string
  }
  organization?: { id: string; slug: string; name: string } | null
  project?: { id: string; slug: string; name: string; repository_full_name?: string | null; repository_path?: string | null } | null
  application_context?: {
    id: string
    status: string
    scanner_version: string
    graph_hash: string
    impact_analysis: Record<string, unknown>
    risk_analysis: Record<string, unknown>
    repositories: { position: number; full_name?: string | null; requested_path?: string | null; resolved_path?: string | null; base_revision?: string | null; status: string; error_code?: string | null; error_message?: string | null; file_count: number; skipped_file_count: number }[]
  } | null
  execution_plan?: ExecutionPlan | null
  costs: Record<string, unknown>
}
type OutcomeStep = {
  step_id: string
  sequence: number
  name: string
  status: string
  outcome?: string | null
  latency_ms?: number | null
  retry_count: number
  agent_name?: string | null
  provider?: string | null
  model?: string | null
  framework?: string | null
  input_tokens?: number | null
  output_tokens?: number | null
  estimated_cost_usd?: number | null
  evidence_count: number
  requirement_count: number
  changed_file_count: number
  test_count: number
  verification_count: number
  failure?: Record<string, unknown> | null
}
type OutcomeAgent = {
  invocation_id: string
  agent_name: string
  role?: string | null
  provider?: string | null
  model?: string | null
  framework?: string | null
  status?: string | null
  outcome?: string | null
  latency_ms?: number | null
  retry_count: number
  input_tokens?: number | null
  output_tokens?: number | null
  estimated_cost_usd?: number | null
  evidence_count: number
  requirement_count: number
  security_finding_count: number
  legacy_attribution: boolean
  details: Record<string, unknown>
  failure?: Record<string, unknown> | null
}
type RunAnalytics = {
  run_id: string
  task_id: string
  project_id?: string | null
  organization_id?: string | null
  status: string
  outcome?: string | null
  latency_ms?: number | null
  retry_count: number
  input_tokens?: number | null
  output_tokens?: number | null
  estimated_cost_usd?: number | null
  cost_estimation_available: boolean
  evidence_pack_count: number
  evidence_coverage_percent?: number | null
  requirement_coverage_percent?: number | null
  policy_blocked?: boolean | null
  approval_count: number
  pending_approval_count: number
  approved_approval_count: number
  rejected_approval_count: number
  security_finding_count?: number | null
  open_security_finding_count?: number | null
  high_critical_security_finding_count?: number | null
  source_run_id?: string | null
  source_snapshot_id?: string | null
  replay_count: number
  changed_file_count: number
  test_count: number
  verification_count: number
  step_count: number
  agent_invocation_count: number
  legacy_data: boolean
  data_state: 'complete' | 'partial' | 'legacy'
  data_completeness: Record<string, boolean>
  details: {
    changed_files?: string[]
    tests?: string[]
    verifications?: string[]
    failures?: Record<string, unknown>[]
    uncovered_requirements?: { id: string; title: string; text: string }[]
    requirement_counts?: { total: number; covered: number; evidence_covered: number }
    security_findings?: Record<string, unknown>[]
    usage?: Record<string, unknown>[]
    snapshot_count?: number
    latest_snapshot_id?: string | null
    replay_run_ids?: string[]
  }
  steps: OutcomeStep[]
  agents: OutcomeAgent[]
  computed_at: string
}
type AggregateAnalytics = {
  scope_type: string
  scope_id: string
  scope_name?: string | null
  run_count: number
  success_count: number
  failure_count: number
  cancelled_count: number
  success_rate_percent?: number | null
  average_latency_ms?: number | null
  estimated_cost_usd?: number | null
  retry_count: number
  legacy_run_count: number
  incomplete_run_count: number
}
type ReplayComparison = {
  source_run_id: string
  replay_run_id: string
  source_snapshot_id: string
  [key: string]: unknown
}

const json = (value: unknown) => JSON.stringify(value, null, 2)
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : 'Not recorded'
const metric = (value?: number | null, suffix = '') => value === null || value === undefined ? 'Not recorded' : `${value.toLocaleString()}${suffix}`
const money = (value?: number | null) => value === null || value === undefined ? 'Not recorded' : `$${value.toFixed(4)}`
const duration = (value?: number | null) => value === null || value === undefined ? 'Not recorded' : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const text = (value: unknown, fallback = 'Not recorded') => typeof value === 'string' && value ? value : fallback

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
    try { return await request<T>(path) } catch { return undefined }
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
      setSelected(current)
      setContext(nextContext)
      setSteps(nextSteps)
      setEvents(nextEvents)
      setApprovals(nextApprovals)
      setEvidence(nextEvidence)
      setAnalytics(nextAnalytics)
      setSnapshots(nextSnapshots)
      setComparison(nextComparison)
      const [projectAggregate, organizationAggregate] = await Promise.all([
        nextContext.project ? optional<AggregateAnalytics>(`/v1/analytics/projects/${nextContext.project.id}`) : undefined,
        nextContext.organization ? optional<AggregateAnalytics>(`/v1/analytics/organizations/${nextContext.organization.id}`) : undefined,
      ])
      setProjectAnalytics(projectAggregate)
      setOrganizationAnalytics(organizationAggregate)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load run')
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
      const current = selected && nextRuns.find((run) => run.id === selected.id)
      if (current) await loadRun(current)
      else if (nextRuns[0]) await loadRun(nextRuns[0])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load runs')
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
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void Promise.all([loadRuns(), loadClients()]).catch((cause) => setError(cause instanceof Error ? cause.message : 'Unable to load operational data')) }, [])

  const action = async (path: string, body?: Record<string, unknown>) => {
    if (!selected) return
    try {
      await request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })
      await loadRun(selected)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Action failed')
    }
  }

  const submitSettings = (event: FormEvent) => {
    event.preventDefault()
    localStorage.setItem('sacm-actor', actor)
    void Promise.all([loadRuns(), loadClients()])
  }

  const plan = context?.execution_plan
  const application = context?.application_context
  const risk = record(application?.risk_analysis)
  const impact = record(application?.impact_analysis)
  const policy = record(plan?.policy_decision)
  const securityFindings = plan?.security_review?.findings || analytics?.details.security_findings || []
  const uncovered = analytics?.details.uncovered_requirements || []
  const failures = analytics?.details.failures || []
  const clarificationItems = context?.task.clarifications || []

  return <main>
    <header>
      <div><p className="eyebrow">SACM CONTROL PLANE</p><h1>Delivery analytics</h1><p className="subtitle">Trace outcomes from intake through evidence and replay.</p></div>
      <button type="button" className="primary" onClick={() => void loadRuns()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh data'}</button>
    </header>
    <form className="connection" onSubmit={submitSettings}>
      <label>API URL<input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
      <label>Actor<input value={actor} onChange={(event) => setActor(event.target.value)} required /></label>
      <label>Bearer token<input value={token} onChange={(event) => setToken(event.target.value)} type="password" placeholder="Optional for local mode" /></label>
      <button type="submit">Connect</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="layout">
      <aside>
        <h2>Runs <span>{runs.length}</span></h2>
        <div className="run-list">{runs.map((run) => <button type="button" key={run.id} aria-pressed={selected?.id === run.id} className={selected?.id === run.id ? 'run active' : 'run'} onClick={() => void loadRun(run)}>
          <strong>{run.status}</strong><span>{run.id.slice(0, 8)}</span><small>{date(run.updated_at)}</small>
        </button>)}</div>
        <h2 className="clients-heading">Clients <span>{clients.length}</span></h2>
        <div className="clients">{clients.map((client) => <div key={client.id}><b>{client.name}</b><small>{client.projects.map((project) => project.repository_full_name || project.name).join(', ') || 'No projects'}</small></div>)}</div>
      </aside>
      <section className="content" aria-live="polite">
        {!selected ? <div className="empty">No authorized runs available.</div> : <>
          <div className="run-title">
            <div><p className="eyebrow">RUN {selected.id}</p><h2>{selected.status}</h2><p>{selected.target_repo_path || 'Repository was not recorded for this legacy run.'}</p>
              {analytics && <span className={`badge ${analytics.data_state}`}>{analytics.data_state} analytics</span>}</div>
            <div className="actions"><button type="button" onClick={() => void action(`/v1/runs/${selected.id}/cancel`)}>Cancel</button><button type="button" onClick={() => void action(`/v1/runs/${selected.id}/resume`)}>Resume</button></div>
          </div>
          <section className="metrics" aria-label="Run outcome metrics">
            <Metric label="Outcome" value={analytics?.outcome || 'In progress'} />
            <Metric label="Latency" value={duration(analytics?.latency_ms)} />
            <Metric label="Cost" value={money(analytics?.estimated_cost_usd)} />
            <Metric label="Retries" value={metric(analytics?.retry_count)} />
            <Metric label="Requirement coverage" value={metric(analytics?.requirement_coverage_percent, '%')} />
            <Metric label="Evidence coverage" value={metric(analytics?.evidence_coverage_percent, '%')} />
            <Metric label="Open security" value={metric(analytics?.open_security_finding_count)} />
            <Metric label="Failures" value={metric(failures.length)} />
          </section>
          {analytics?.data_state !== 'complete' && <p className="notice">Some fields are unavailable because this run predates durable analytics or did not persist every source signal. Missing values are shown as “Not recorded” rather than zero.</p>}
          <section className="grid">
            <Panel title="Client, project & task" wide><dl className="metadata">
              <Meta label="Client" value={context?.organization?.name || 'Not linked (legacy)'} />
              <Meta label="Project" value={context?.project?.name || 'Not linked (legacy)'} />
              <Meta label="Repository" value={context?.project?.repository_full_name || context?.project?.repository_path || selected.target_repo_path || 'Not recorded'} />
              <Meta label="Task" value={context?.task.title || 'Not recorded'} />
              <Meta label="Task status" value={context?.task.status || 'Not recorded'} />
              <Meta label="Connector" value={context?.task.connector_type || 'Not recorded'} />
              <Meta label="External ID" value={context?.task.external_id || 'Not recorded'} />
              <Meta label="Workflow" value={context?.run.workflow_version || selected.workflow_version || 'Not recorded'} />
              <Meta label="Source revision" value={context?.run.source_revision || 'Not recorded'} />
              <div className="wide"><dt>Description</dt><dd>{context?.task.description || 'Not recorded'}</dd></div>
            </dl></Panel>

            <Panel title="Readiness & clarifications"><div className="summary-row"><Status value={record(context?.task.readiness_details).ready === true ? 'READY' : record(context?.task.readiness_details).ready === false ? 'NOT READY' : 'NOT RECORDED'} /><b>{metric(context?.task.readiness_score === undefined || context.task.readiness_score === null ? null : context.task.readiness_score * 100, '%')}</b></div>
              {context?.task.readiness_details ? <KeyValues value={context.task.readiness_details} /> : <MissingData text="Definition-of-Ready details were not persisted." />}
              <h4>Clarifications</h4>
              {clarificationItems.length ? clarificationItems.map((item) => <div className="list-item" key={item.id}><div><b>{item.question}</b><span>{item.field_name} · {item.status}</span></div><p>{item.answer === undefined || item.answer === null ? 'No answer recorded.' : typeof item.answer === 'string' ? item.answer : json(item.answer)}</p></div>) : <MissingData text="No clarification records." />}
            </Panel>

            <Panel title="Application context"><div className="summary-row"><Status value={application?.status || 'NOT BUILT'} /><span>Risk: <b>{text(risk.level)}</b> · score {metric(typeof risk.score === 'number' ? risk.score : null)}</span></div>
              {application ? <>
                <dl className="metadata"><Meta label="Scanner" value={application.scanner_version} /><Meta label="Graph hash" value={application.graph_hash} /><Meta label="Impacted repositories" value={metric(typeof impact.impacted_repository_count === 'number' ? impact.impacted_repository_count : null)} /><Meta label="Impacted nodes" value={metric(Array.isArray(impact.impacted_nodes) ? impact.impacted_nodes.length : null)} /></dl>
                <h4>Repositories</h4>
                {application.repositories.map((repository) => <div className="list-item" key={`${repository.position}-${repository.full_name || repository.requested_path}`}><div><b>{repository.full_name || repository.requested_path || 'Unnamed repository'}</b><Status value={repository.status} /></div><span>{repository.resolved_path || 'Path unavailable'} · {repository.file_count} files · revision {repository.base_revision || 'not recorded'}</span>{repository.error_message && <p className="danger-text">{repository.error_message}</p>}</div>)}
                <details><summary>Impact and risk detail</summary><pre>{json({ impact_analysis: impact, risk_analysis: risk })}</pre></details>
              </> : <MissingData text="Application context was not built or this is a legacy run." />}
            </Panel>

            <Panel title="Execution plan" wide>{plan ? <>
              <div className="summary-row"><div><Status value={plan.status} /><span>Revision {plan.revision} · policy {plan.policy_pack}</span></div><span>{plan.steps.length} planned steps</span></div>
              <div className="table-wrap"><table><thead><tr><th>#</th><th>Step</th><th>Assigned agent</th><th>Provider / model / framework</th><th>Risk & tools</th></tr></thead><tbody>{plan.steps.map((item) => {
                const configuration = record(item.agent.configuration)
                return <tr key={item.id}><td>{item.sequence}</td><td><b>{item.title}</b><span>{item.kind}</span><small>{item.objective}</small></td><td>{item.agent.agent_name || 'Not assigned'}<span>{item.agent.role || 'Role not recorded'}</span></td><td>{text(configuration.provider)}<span>{text(configuration.model || configuration.model_name)} · {text(configuration.framework || item.agent.runtime_kind)}</span></td><td>{item.risk_tags.join(', ') || 'No risk tags'}<span>{item.required_tools.join(', ') || 'No tools recorded'}</span></td></tr>
              })}</tbody></table></div>
            </> : <MissingData text="No durable execution plan is available." />}</Panel>

            <Panel title="Step outcomes" wide>{analytics?.steps.length ? <div className="table-wrap"><table><thead><tr><th>#</th><th>Step</th><th>Outcome</th><th>Agent</th><th>Latency</th><th>Usage / evidence</th><th>Action</th></tr></thead><tbody>{analytics.steps.map((step) => <tr key={step.step_id}><td>{step.sequence}</td><td><b>{step.name}</b><span>{step.status}</span></td><td><Status value={step.outcome || 'IN PROGRESS'} />{step.failure && <small className="danger-text">{json(step.failure)}</small>}</td><td>{step.agent_name || 'Not recorded'}<span>{step.provider || 'Provider not recorded'} · {step.model || 'Model not recorded'} · {step.framework || 'Framework not recorded'}</span></td><td>{duration(step.latency_ms)}<span>{step.retry_count} retries</span></td><td>{metric(step.input_tokens)} in / {metric(step.output_tokens)} out<span>{money(step.estimated_cost_usd)} · {step.evidence_count} evidence</span></td><td>{step.status === 'FAILED' ? <button type="button" onClick={() => void action(`/v1/runs/${selected.id}/steps/${step.step_id}/retry`)}>Retry</button> : '—'}</td></tr>)}</tbody></table></div> : steps.length ? <MissingData text="Steps exist, but outcome analytics have not been materialized." /> : <MissingData text="No run steps were persisted." />}</Panel>

            <Panel title="Invoked agents">{analytics?.agents.length ? analytics.agents.map((agent) => <div className="agent" key={agent.invocation_id}><div className="summary-row"><div><b>{agent.agent_name}</b><span>{agent.role || 'Role not recorded'} · {agent.framework || 'Framework not recorded'}</span></div><Status value={agent.outcome || agent.status || 'NOT RECORDED'} /></div><p>{text(agent.details.summary, 'No summary recorded.')}</p><small>{agent.provider || 'Provider not recorded'} / {agent.model || 'Model not recorded'} · {metric(agent.input_tokens)} input · {metric(agent.output_tokens)} output · {money(agent.estimated_cost_usd)}</small>{agent.legacy_attribution && <span className="badge legacy">legacy attribution</span>}</div>) : <MissingData text="No agent_result event was recorded. System and user runtime actors are intentionally not counted as agents." />}</Panel>

            <Panel title="Policy, security & approvals"><div className="summary-row"><Status value={analytics?.policy_blocked === true ? 'BLOCKED' : analytics?.policy_blocked === false ? 'ALLOWED' : 'NOT RECORDED'} /><span>{analytics?.approval_count ?? 0} approvals · {analytics?.pending_approval_count ?? 0} pending</span></div>
              {plan ? <details><summary>Policy and risk decision</summary><pre>{json({ policy, risk_decision: plan.risk_decision, approval_gates: plan.approval_gates })}</pre></details> : <MissingData text="No policy decision was persisted." />}
              <h4>Security review</h4>
              {plan?.security_review ? <div className="list-item"><div><Status value={plan.security_review.status} /><span>{plan.security_review.reviewed_by || plan.security_review.reviewer.agent_name || 'Reviewer not recorded'}</span></div><span>{securityFindings.length} findings · reviewed {date(plan.security_review.reviewed_at)}</span></div> : <MissingData text="Security review was not persisted." />}
              {securityFindings.map((finding, index) => <div className="finding" key={text(finding.finding_id, String(index))}><Status value={text(finding.severity, 'unknown')} /><div><b>{text(finding.title, 'Untitled finding')}</b><p>{text(finding.description, 'Description not recorded.')}</p></div></div>)}
              <h4>Run approvals</h4>
              {approvals.length ? approvals.map((approval) => <div className="approval" key={approval.id}><div><b>{approval.action}</b><span>{approval.status} · requested {date(approval.requested_at)}</span></div>{approval.status === 'PENDING' && <div><button type="button" onClick={() => void action(`/v1/approvals/${approval.id}/decision`, { approve: true, reason: 'Approved from SACM dashboard.' })}>Approve</button><button type="button" onClick={() => void action(`/v1/approvals/${approval.id}/decision`, { approve: false, reason: 'Rejected from SACM dashboard.' })}>Reject</button></div>}</div>) : <MissingData text="No approvals were recorded." />}
            </Panel>

            <Panel title="Requirement coverage"><div className="coverage"><label>Requirement coverage <b>{metric(analytics?.requirement_coverage_percent, '%')}</b><progress max="100" value={analytics?.requirement_coverage_percent ?? 0} /></label><label>Evidence coverage <b>{metric(analytics?.evidence_coverage_percent, '%')}</b><progress max="100" value={analytics?.evidence_coverage_percent ?? 0} /></label></div>
              {analytics?.details.requirement_counts ? <p>{analytics.details.requirement_counts.covered} of {analytics.details.requirement_counts.total} covered; {analytics.details.requirement_counts.evidence_covered} have evidence.</p> : <MissingData text="No normalized requirements were persisted." />}
              <h4>Uncovered requirements</h4>
              {uncovered.length ? <ul className="requirements">{uncovered.map((item) => <li key={item.id}><b>{item.title}</b><span>{item.text}</span></li>)}</ul> : analytics?.details.requirement_counts?.total ? <p>All normalized requirements have a material traceability link.</p> : <MissingData text="Coverage cannot be calculated without durable requirements." />}
            </Panel>

            <Panel title="Evidence, changes & verification"><dl className="metadata"><Meta label="Evidence packs" value={metric(analytics?.evidence_pack_count)} /><Meta label="Changed files" value={metric(analytics?.changed_file_count)} /><Meta label="Tests" value={metric(analytics?.test_count)} /><Meta label="Verifications" value={metric(analytics?.verification_count)} /></dl>
              <h4>Evidence packs</h4>{evidence.length ? evidence.map((item) => <div className="evidence" key={item.id}><div><b>{item.manifest_hash.slice(0, 16)}…</b><span>{date(item.created_at)}</span></div><span>{item.path}</span></div>) : <MissingData text="No evidence package was recorded." />}
              <h4>Changed files</h4><TagList values={analytics?.details.changed_files} empty="No changed files were persisted." />
              <details><summary>Test and verification identifiers</summary><pre>{json({ tests: analytics?.details.tests || [], verifications: analytics?.details.verifications || [] })}</pre></details>
            </Panel>

            <Panel title="Cost & token usage"><dl className="metadata"><Meta label="Input tokens" value={metric(analytics?.input_tokens)} /><Meta label="Output tokens" value={metric(analytics?.output_tokens)} /><Meta label="Estimated cost" value={money(analytics?.estimated_cost_usd)} /><Meta label="Estimate available" value={analytics?.cost_estimation_available ? 'Yes' : 'No'} /></dl>
              {analytics?.details.usage?.length ? <div className="usage-list">{analytics.details.usage.map((item, index) => <div className="list-item" key={index}><b>{text(item.agent_name)} · {text(item.provider)} / {text(item.model)}</b><span>{metric(typeof item.input_tokens === 'number' ? item.input_tokens : null)} input · {metric(typeof item.output_tokens === 'number' ? item.output_tokens : null)} output · {money(typeof item.estimated_cost_usd === 'number' ? item.estimated_cost_usd : null)}</span></div>)}</div> : <MissingData text="Provider usage was not recorded; cost is intentionally null." />}
            </Panel>

            <Panel title="Snapshots, replay & comparison"><dl className="metadata"><Meta label="Snapshots" value={metric(snapshots.length)} /><Meta label="Replays created" value={metric(analytics?.replay_count)} /><Meta label="Source run" value={analytics?.source_run_id || 'Not a replay'} /><Meta label="Source snapshot" value={analytics?.source_snapshot_id || 'Not a replay'} /></dl>
              {snapshots.length ? <ol className="snapshot-list">{snapshots.map((snapshot) => <li key={snapshot.id}><b>{snapshot.creation_reason}</b><span>Event {snapshot.event_sequence} · {date(snapshot.created_at)}</span><small>{snapshot.id}</small></li>)}</ol> : <MissingData text="No snapshots are available for this run." />}
              {comparison ? <details open><summary>Replay comparison</summary><pre>{json(comparison)}</pre></details> : <MissingData text="Comparison is available only for a replay-linked run." />}
            </Panel>

            <Panel title="Project & organization aggregates"><div className="aggregate-grid"><Aggregate title="Project" value={projectAnalytics} /><Aggregate title="Organization" value={organizationAnalytics} /></div></Panel>

            <Panel title="Failures">{failures.length ? failures.map((failure, index) => <pre className="failure" key={index}>{json(failure)}</pre>) : <p>No persisted failure was found.</p>}</Panel>

            <Panel title="Runtime timeline" wide><ol className="timeline">{events.map((event) => <li key={event.id}><b>{event.event_type}</b><span>{event.actor} · {date(event.occurred_at)}</span><details><summary>Payload</summary><code>{json(event.payload)}</code></details></li>)}</ol></Panel>
          </section>
        </>}
      </section>
    </section>
  </main>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div> }
function Panel({ title, children, wide = false }: { title: string; children: ReactNode; wide?: boolean }) { return <article className={wide ? 'wide-panel' : undefined}><h3>{title}</h3>{children}</article> }
function Meta({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div> }
function Status({ value }: { value: string }) { const normalized = value.toLowerCase().replaceAll('_', '-'); return <span className={`status ${normalized}`}>{value}</span> }
function MissingData({ text: value }: { text: string }) { return <p className="missing">{value}</p> }
function TagList({ values, empty }: { values?: string[]; empty: string }) { return values?.length ? <div className="tags">{values.map((value) => <span key={value}>{value}</span>)}</div> : <MissingData text={empty} /> }
function KeyValues({ value }: { value: Record<string, unknown> }) { return <dl className="metadata compact">{Object.entries(value).map(([key, item]) => <Meta key={key} label={key.replaceAll('_', ' ')} value={typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? String(item) : json(item)} />)}</dl> }
function Aggregate({ title, value }: { title: string; value?: AggregateAnalytics }) { return <div className="aggregate"><h4>{title}</h4>{value ? <dl><Meta label="Runs" value={metric(value.run_count)} /><Meta label="Success rate" value={metric(value.success_rate_percent, '%')} /><Meta label="Average latency" value={duration(value.average_latency_ms)} /><Meta label="Estimated cost" value={money(value.estimated_cost_usd)} /><Meta label="Retries" value={metric(value.retry_count)} /><Meta label="Legacy / incomplete" value={`${value.legacy_run_count} / ${value.incomplete_run_count}`} /></dl> : <MissingData text="No authorized aggregate is available." />}</div> }

export default App
