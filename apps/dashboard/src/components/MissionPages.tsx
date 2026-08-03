import type { FormEvent } from 'react'
import { date, json, metric, money, record, text } from '../formatters'
import type {
  DashboardProps,
  ExpertBenchmarkAssessment,
  OutcomeAgent,
  Run,
  RunAnalytics,
} from '../types'
import { Meta, MissingData, Status, TagList } from './DashboardPrimitives'
import { LiveProgress } from './LiveProgress'

const average = (values: (number | null | undefined)[]) => {
  const recorded = values.filter((value): value is number => value !== null && value !== undefined)
  return recorded.length ? recorded.reduce((sum, value) => sum + value, 0) / recorded.length : null
}

const scalar = (value: unknown) =>
  typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? String(value)
    : 'Not recorded'

const statusOf = (value: unknown) => text(record(value).status, 'NOT RECORDED')

function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: React.ReactNode }) {
  return <header className="page-header">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>
}

function DataState({ analytics }: { analytics?: RunAnalytics }) {
  if (!analytics || analytics.data_state === 'complete') return null
  return <p className="data-notice"><b>{analytics.data_state.toUpperCase()} DATA</b> Missing legacy signals remain “Not recorded”; SACM does not infer them.</p>
}

export function CommandCenterPage(props: DashboardProps & { onMission: (run: Run) => void }) {
  const { runs, portfolioAnalytics, operationalHealth, executorFleet, loading, loadRuns, onMission } = props
  const successes = portfolioAnalytics.filter((item) => item.outcome === 'SUCCESS').length
  const failures = portfolioAnalytics.filter((item) => item.outcome === 'FAILURE').length
  const blockedPolicies = portfolioAnalytics.filter((item) => item.policy_blocked === true).length
  const blockedSecurity = portfolioAnalytics.reduce((sum, item) => sum + (item.high_critical_security_finding_count || 0), 0)
  const costs = portfolioAnalytics.filter((item) => item.cost_estimation_available).map((item) => item.estimated_cost_usd)
  const cost = costs.some((item) => item !== null && item !== undefined)
    ? costs.reduce<number>((sum, item) => sum + (item || 0), 0)
    : null
  const requirementCoverage = average(portfolioAnalytics.map((item) => item.requirement_coverage_percent))
  const evidenceCoverage = average(portfolioAnalytics.map((item) => item.evidence_coverage_percent))
  const measuredOutcomes = successes + failures + portfolioAnalytics.filter((item) => item.outcome === 'CANCELLED').length
  const acceptedProxyRate = measuredOutcomes ? (successes / measuredOutcomes) * 100 : null
  const legacyCount = portfolioAnalytics.filter((item) => item.legacy_data || item.data_state !== 'complete').length
  const executorSlo = record(executorFleet?.slo)
  const healthChecks = [
    ['Executor SLO', executorSlo.met === true ? 'HEALTHY' : executorSlo.met === false ? 'UNHEALTHY' : 'NOT RECORDED'],
    ['Backup', statusOf(operationalHealth?.backup)],
    ['Audit chain', statusOf(operationalHealth?.audit)],
    ['Governance', statusOf(operationalHealth?.governance)],
  ]

  return <>
    <PageHeader
      eyebrow="MISSION CONTROL"
      title="Command Center"
      description="Authorized delivery outcomes, capacity and trust signals from SACM APIs."
      actions={<button type="button" className="primary" onClick={() => void loadRuns()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh telemetry'}</button>}
    />
    <section className="command-metrics" aria-label="Portfolio metrics">
      <div><span>Authorized missions</span><strong>{metric(runs.length)}</strong><small>{portfolioAnalytics.length} with outcome analytics</small></div>
      <div><span>Accepted proxy</span><strong>{metric(acceptedProxyRate, '%')}</strong><small>{successes} SUCCESS outcomes; not human acceptance</small></div>
      <div><span>Failed outcomes</span><strong>{metric(failures)}</strong><small>Persisted FAILURE outcomes only</small></div>
      <div><span>Estimated cost</span><strong>{money(cost)}</strong><small>Recorded provider estimates</small></div>
      <div><span>Requirement coverage</span><strong>{metric(requirementCoverage, '%')}</strong><small>Mean of recorded runs</small></div>
      <div><span>Evidence coverage</span><strong>{metric(evidenceCoverage, '%')}</strong><small>Mean of recorded runs</small></div>
      <div><span>Policy blocks / high-critical findings</span><strong>{blockedPolicies} / {blockedSecurity}</strong><small>Recorded policy blocks · open high/critical findings</small></div>
      <div><span>Executor capacity</span><strong>{metric(executorFleet?.capacity.available_slots)}</strong><small>{executorFleet ? `${executorFleet.active}/${executorFleet.total} executors active` : 'Not authorized or not configured'}</small></div>
    </section>
    {(portfolioAnalytics.length < runs.length || legacyCount > 0) && <p className="data-notice"><b>PARTIAL / LEGACY PORTFOLIO</b> {runs.length - portfolioAnalytics.length} missions lack authorized outcome analytics; {legacyCount} analytics records report partial or legacy source data. Missing values are not treated as zero.</p>}
    <section className="command-grid">
      <article className="surface health-surface">
        <div className="section-head"><div><p className="eyebrow">OPERATIONAL ASSURANCE</p><h2>SLO, backup and audit health</h2></div><Status value={operationalHealth?.status || 'NOT RECORDED'} /></div>
        <div className="health-grid">{healthChecks.map(([label, value]) => <div key={label}><span>{label}</span><Status value={value || 'NOT RECORDED'} /></div>)}</div>
        <dl className="metadata compact">
          <Meta label="Queue depth" value={scalar(record(operationalHealth?.queue).depth)} />
          <Meta label="Oldest queued work" value={record(operationalHealth?.queue).oldest_age_seconds === undefined ? 'Not recorded' : `${scalar(record(operationalHealth?.queue).oldest_age_seconds)} s`} />
          <Meta label="Active executors" value={metric(executorFleet?.active)} />
          <Meta label="Available job slots" value={metric(executorFleet?.capacity.available_slots)} />
        </dl>
      </article>
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">RECENT MISSIONS</p><h2>Delivery pulse</h2></div><span className="count-pill">{runs.length}</span></div>
        <div className="mission-table">{runs.slice(0, 7).map((run) => {
          const analytics = portfolioAnalytics.find((item) => item.run_id === run.id)
          return <button type="button" key={run.id} onClick={() => onMission(run)}>
            <span><b>{run.task_id.slice(0, 10)}</b><small>{date(run.updated_at)}</small></span>
            <Status value={analytics?.outcome || run.status} />
            <span><b>{money(analytics?.estimated_cost_usd)}</b><small>{metric(analytics?.requirement_coverage_percent, '%')} requirements</small></span>
          </button>
        })}</div>
      </article>
    </section>
  </>
}

function Readiness({ props }: { props: DashboardProps }) {
  const details = record(props.context?.task.readiness_details)
  const checks = record(details.checks)
  const missing = Array.isArray(details.missing_fields) ? details.missing_fields.map(String) : []
  const score = props.context?.task.readiness_score === null || props.context?.task.readiness_score === undefined
    ? null
    : Math.round(props.context.task.readiness_score * 100)
  const criteria = ['description', 'acceptance_criteria', 'repositories', 'requested_by']
  return <article className="surface readiness-card">
    <div className="section-head">
      <div><p className="eyebrow">DEFINITION OF READY</p><h2>{score === null ? 'Assessment unavailable' : score >= 80 ? 'Ready for execution' : 'Clarification required'}</h2></div>
      <div className="score-gauge" style={{ '--score': score || 0 } as React.CSSProperties}><strong>{metric(score)}</strong><span>/100</span></div>
    </div>
    <progress value={score || 0} max="100" aria-label={`Readiness ${score === null ? 'not recorded' : `${score} percent`}`} />
    <div className="criteria-grid">{criteria.map((criterion) => {
      const passed = checks[criterion] === true
      const unknown = checks[criterion] === undefined
      return <div className={passed ? 'criterion passed' : unknown ? 'criterion unknown' : 'criterion missing'} key={criterion}>
        <span aria-hidden="true">{passed ? '✓' : unknown ? '–' : '!'}</span>
        <div><b>{criterion.replaceAll('_', ' ')}</b><small>{passed ? 'Satisfied by task contract' : unknown ? 'Not assessed in this contract version' : 'Required before autonomous execution'}</small></div>
      </div>
    })}</div>
    {missing.length > 0 && <p className="readiness-summary"><b>Missing:</b> {missing.join(', ')}</p>}
    <div className="clarification-list">
      <h3>Actionable clarifications</h3>
      {props.context?.task.clarifications?.length
        ? props.context.task.clarifications.map((item) => <article key={item.id}>
            <span className={item.status.toLowerCase()}>{item.status === 'answered' || item.status === 'ANSWERED' ? '✓' : '?'}</span>
            <div><b>{item.question}</b><small>Field: {item.field_name.replaceAll('_', ' ')}</small>{item.answer !== undefined && item.answer !== null && <p>{typeof item.answer === 'string' ? item.answer : json(item.answer)}</p>}</div>
          </article>)
        : <MissingData text="No clarification records are attached to this task." />}
    </div>
  </article>
}

function AutonomyDecision({ props }: { props: DashboardProps }) {
  const plan = props.context?.execution_plan
  const policy = record(plan?.policy_decision)
  const risk = record(plan?.risk_decision)
  const pendingGates = plan?.approval_gates.filter((gate) => !['APPROVED', 'PASSED'].includes(gate.status.toUpperCase())) || []
  const securityBlocks = props.analytics?.high_critical_security_finding_count || 0
  const policyText = `${text(policy.decision, '')} ${text(policy.status, '')} ${text(policy.effect, '')}`.toUpperCase()
  const blocked = props.analytics?.policy_blocked === true || securityBlocks > 0 || policyText.includes('BLOCK')
  const approvalRequired = !blocked && (pendingGates.length > 0 || (props.analytics?.pending_approval_count || 0) > 0)
  const state = blocked ? 'BLOCKED' : approvalRequired ? 'APPROVAL REQUIRED' : 'SUGGESTED'
  const reasons = [
    ...pendingGates.map((gate) => gate.reason),
    ...((props.fullApplication?.risk_analysis.factors || []).map((factor) => factor.explanation)),
    ...(securityBlocks ? [`${securityBlocks} high or critical security findings are open.`] : []),
    ...(!plan ? ['No durable execution plan is available.'] : []),
  ]
  return <article className={`surface autonomy-card ${state.toLowerCase().replaceAll(' ', '-')}`}>
    <div className="section-head"><div><p className="eyebrow">RISK-BASED AUTONOMY</p><h2>{state}</h2></div><Status value={state} /></div>
    <p>{blocked ? 'SACM has a recorded policy or security reason that prevents execution.' : approvalRequired ? 'A human decision is required before the affected step can continue.' : 'The recorded risk and policy state permits a suggested next action; no action is taken from this panel.'}</p>
    {reasons.length ? <ul>{reasons.slice(0, 5).map((reason) => <li key={reason}>{reason}</li>)}</ul> : <MissingData text="No autonomy reasons were recorded." />}
    <details><summary>Recorded policy and risk decisions</summary><pre>{json({ policy, risk, approval_gates: plan?.approval_gates || [] })}</pre></details>
  </article>
}

function Journey({ props }: { props: DashboardProps }) {
  const completed = props.steps.filter((step) => step.status === 'COMPLETED').length
  const stages = [
    ['Intake', Boolean(props.context?.task)],
    ['Ready', record(props.context?.task.readiness_details).ready === true],
    ['Plan', Boolean(props.context?.execution_plan)],
    ['Execute', completed > 0],
    ['Verify', Boolean(props.analytics?.verification_count)],
    ['Approve', Boolean(props.analytics?.approved_approval_count)],
    ['Deliver', props.selected?.status === 'COMPLETED'],
  ]
  return <section className="journey" aria-label="Live Change Journey">
    {stages.map(([label, complete], index) => <div className={complete ? 'journey-stage done' : index === stages.findIndex(([, value]) => !value) ? 'journey-stage active' : 'journey-stage'} key={String(label)}>
      <span>{complete ? '✓' : index + 1}</span><b>{label}</b>{index < stages.length - 1 && <i />}
    </div>)}
  </section>
}

export function MissionsPage(props: DashboardProps) {
  const { runs, selected, context, analytics, steps, events, approvals, snapshots, executionJobs, comparison, action, loadRun, loading } = props
  if (!selected) return <><PageHeader eyebrow="MISSIONS" title="Mission View" description="No authorized missions are available." /><MissingData text="Connect an authorized tenant in Settings or create a mission through the API." /></>
  const plan = context?.execution_plan
  const taskSource = context?.task.connector_type || 'Not recorded'
  const activeStep = steps.find((step) => ['RUNNING', 'IN_PROGRESS'].includes(step.status))
  const telemetry = props.lifecycleMetrics?.telemetry
  const providerUsageAvailable = Boolean(telemetry?.usage.length)
  const inputTokens = analytics?.input_tokens ?? telemetry?.input_tokens
  const outputTokens = analytics?.output_tokens ?? telemetry?.output_tokens
  const estimatedCost = analytics?.estimated_cost_usd ?? (
    telemetry?.cost_estimation_available ? telemetry.estimated_cost_usd : null
  )
  const canResume = selected.status === 'FAILED'
  const canCancel = !['COMPLETED', 'CANCELLED'].includes(selected.status)
  return <>
    <PageHeader
      eyebrow={`MISSION ${context?.task.external_id || selected.task_id.slice(0, 10)}`}
      title={context?.task.title || 'Untitled mission'}
      description={context?.task.description || 'Task description was not recorded.'}
      actions={<><Status value={selected.status} />{canResume && <button type="button" onClick={() => void action(`/v1/runs/${selected.id}/resume`)}>Resume failed run</button>}{canCancel && <button type="button" onClick={() => void action(`/v1/runs/${selected.id}/cancel`)}>Cancel run</button>}</>}
    />
    <div className="mission-picker" role="list" aria-label="Authorized missions">{runs.map((run) => <button type="button" role="listitem" key={run.id} className={run.id === selected.id ? 'active' : ''} onClick={() => void loadRun(run)} disabled={loading}>
      <b>{run.task_id.slice(0, 8)}</b><Status value={run.status} /><small>{date(run.updated_at)}</small>
    </button>)}</div>
    <DataState analytics={analytics} />
    <LiveProgress progress={props.progress} error={props.progressError} />
    <section className="mission-facts" aria-label="Mission identity">
      <div><span>Source</span><strong>{taskSource}</strong><small>{context?.task.external_url ? <a href={context.task.external_url} target="_blank" rel="noreferrer">{context.task.external_id || 'Open source task'}</a> : context?.task.external_id || 'External ID not recorded'}</small></div>
      <div><span>Application</span><strong>{context?.project?.name || 'Not linked'}</strong><small>{context?.project?.repository_full_name || 'Repository not recorded'}</small></div>
      <div><span>Current step</span><strong>{activeStep?.name || (selected.status === 'COMPLETED' ? 'Completed' : 'Not recorded')}</strong><small>{steps.length} execution steps</small></div>
      <div><span>Cost</span><strong>{money(estimatedCost)}</strong><small>{metric(inputTokens)} input · {metric(outputTokens)} output tokens</small></div>
    </section>
    <Journey props={props} />
    <section className="two-column">
      <Readiness props={props} />
      <AutonomyDecision props={props} />
    </section>
    <section className="two-column">
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">PLAN & EXECUTION</p><h2>{plan ? `Revision ${plan.revision}` : 'No plan recorded'}</h2></div><Status value={plan?.status || 'NOT RECORDED'} /></div>
        {plan ? <ol className="plan-steps">{plan.steps.map((item) => {
          const outcome = analytics?.steps.find((step) => step.step_id === item.id || step.sequence === item.sequence)
          const configuration = record(item.agent.configuration)
          return <li key={item.id}><span>{item.sequence}</span><div><b>{item.title}</b><p>{item.objective}</p><small>{item.agent.agent_name || 'Unassigned'} · {text(configuration.provider)} / {text(configuration.model || configuration.model_name)} · {text(configuration.framework || item.agent.runtime_kind)}</small><TagList values={[...item.risk_tags, ...item.required_tools]} empty="No risk tags or tools recorded." /></div><Status value={outcome?.outcome || outcome?.status || 'PLANNED'} /></li>
        })}</ol> : <MissingData text="No durable execution plan is available for this mission." />}
      </article>
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">JOBS & AGENTS</p><h2>Execution plane</h2></div><span className="count-pill">{executionJobs.length}</span></div>
        {executionJobs.length ? executionJobs.map((job) => <div className="job-row" key={job.id}><div><b>{job.id.slice(0, 12)}</b><small>Step {job.run_step_id.slice(0, 8)} · attempt {job.attempt ?? 'not recorded'}</small></div><Status value={job.state} /></div>) : <MissingData text="No authorized execution jobs were returned. Operations-level access may be required." />}
        <h3>Agent activity</h3>
        {analytics?.agents.length ? analytics.agents.map((agent) => <AgentRow agent={agent} key={agent.invocation_id} />) : <MissingData text="No persisted agent_result events are available." />}
      </article>
    </section>
    <section className="two-column">
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">TESTS & TRACEABILITY</p><h2>Verification coverage</h2></div><strong>{metric(analytics?.evidence_coverage_percent, '%')}</strong></div>
        <div className="coverage-bars">
          <label>Requirements <b>{metric(analytics?.requirement_coverage_percent, '%')}</b><progress max="100" value={analytics?.requirement_coverage_percent || 0} /></label>
          <label>Evidence <b>{metric(analytics?.evidence_coverage_percent, '%')}</b><progress max="100" value={analytics?.evidence_coverage_percent || 0} /></label>
        </div>
        <dl className="metadata"><Meta label="Tests" value={metric(analytics?.test_count)} /><Meta label="Verifications" value={metric(analytics?.verification_count)} /><Meta label="Changed files" value={metric(analytics?.changed_file_count)} /><Meta label="Evidence packs" value={metric(analytics?.evidence_pack_count)} /></dl>
        <TagList values={analytics?.details.tests} empty="No test identifiers were recorded." />
      </article>
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">SNAPSHOTS & REPLAY</p><h2>Reproducible journey</h2></div><span className="count-pill">{snapshots.length}</span></div>
        {snapshots.length ? <ol className="snapshot-list">{snapshots.map((snapshot) => <li key={snapshot.id}><b>{snapshot.creation_reason}</b><span>Event {snapshot.event_sequence} · {date(snapshot.created_at)}</span><small>{snapshot.checksum}</small></li>)}</ol> : <MissingData text="No snapshots were recorded for this mission." />}
        {comparison ? <details><summary>Replay comparison</summary><pre>{json(comparison)}</pre></details> : <p className="quiet">No replay comparison is available.</p>}
      </article>
    </section>
    <article className="surface">
      <div className="section-head"><div><p className="eyebrow">RECORDED TELEMETRY</p><h2>Usage and execution metrics</h2></div><span className="count-pill">{telemetry?.usage.length || 0}</span></div>
      {telemetry ? <>{!providerUsageAvailable && <p className="data-notice"><b>PROVIDER USAGE UNAVAILABLE</b> This executor did not emit token or cost events. SACM reports N/A rather than inferring zero usage.</p>}<dl className="metadata"><Meta label="Input tokens" value={providerUsageAvailable ? metric(telemetry.input_tokens) : 'N/A'} /><Meta label="Output tokens" value={providerUsageAvailable ? metric(telemetry.output_tokens) : 'N/A'} /><Meta label="Estimated cost" value={telemetry.cost_estimation_available ? money(telemetry.estimated_cost_usd) : 'N/A'} /><Meta label="Premium requests" value={metric(telemetry.premium_requests)} /><Meta label="Copilot AIU" value={metric(telemetry.total_nano_aiu / 1_000_000_000)} /><Meta label="Tool executions" value={metric(telemetry.tool_execution_count)} /><Meta label="Tool duration" value={`${metric(telemetry.tool_duration_ms)} ms`} /><Meta label="Failed tools" value={metric(telemetry.failed_tool_execution_count)} /></dl></> : <MissingData text="No durable usage or execution telemetry is available for this mission." />}
    </article>
    <article className="surface timeline-surface">
      <div className="section-head"><div><p className="eyebrow">LIVE CHANGE JOURNEY</p><h2>Event timeline</h2></div><span className="count-pill">{events.length}</span></div>
      {events.length ? <ol className="event-timeline">{events.map((event) => <li key={event.id}><span>{event.sequence}</span><div><b>{event.event_type.replaceAll('_', ' ')}</b><small>{event.actor} · {date(event.occurred_at)}</small></div><details><summary>Payload</summary><pre>{json(event.payload)}</pre></details></li>)}</ol> : <MissingData text="No runtime events were recorded." />}
    </article>
    {approvals.length > 0 && <article className="surface"><div className="section-head"><div><p className="eyebrow">APPROVALS</p><h2>Human decisions</h2></div></div>{approvals.map((approval) => <div className="approval-row" key={approval.id}><div><b>{approval.action}</b><small>{date(approval.requested_at)}</small></div><Status value={approval.status} /></div>)}</article>}
  </>
}

function AgentRow({ agent }: { agent: OutcomeAgent }) {
  return <div className="agent-row"><div className="agent-avatar">{agent.agent_name.slice(0, 1).toUpperCase()}</div><div><b>{agent.agent_name}</b><small>{agent.role || 'Role not recorded'} · {agent.provider || 'Provider not recorded'} / {agent.model || agent.framework || 'Model not recorded'}</small></div><Status value={agent.outcome || agent.status || 'NOT RECORDED'} /></div>
}

const groupOrder = ['repository', 'module', 'api', 'database', 'dependency']

const normalizedGroup = (type: string) => {
  const value = type.toLowerCase()
  if (value.includes('repo')) return 'repository'
  if (value.includes('api') || value.includes('route') || value.includes('endpoint')) return 'api'
  if (value.includes('database') || value.includes('table') || value.includes('model')) return 'database'
  if (value.includes('dependency') || value.includes('package') || value.includes('external')) return 'dependency'
  return 'module'
}

export function ApplicationsPage(props: DashboardProps) {
  const application = props.fullApplication
  const impacted = new Set(application?.impact_analysis.impacted_nodes.map((item) => item.node_id) || [])
  const groups = groupOrder.map((group) => [group, application?.graph.nodes.filter((node) => normalizedGroup(node.type) === group) || []] as const)
  return <>
    <PageHeader eyebrow="APPLICATION INTELLIGENCE" title="Application Map" description="Accessible dependency topology from the deterministic application-context graph." />
    {!application ? <MissingData text="No application context is available for the selected mission." /> : <>
      <section className="application-summary">
        <div><span>Graph status</span><Status value={application.status} /></div><div><span>Nodes</span><strong>{application.graph.nodes.length}</strong></div><div><span>Edges</span><strong>{application.graph.edges.length}</strong></div><div><span>Impacted</span><strong>{impacted.size}</strong></div><div><span>Risk</span><Status value={`${application.risk_analysis.level} ${application.risk_analysis.score}`} /></div>
      </section>
      {application.graph.truncated && <p className="data-notice"><b>TRUNCATED GRAPH</b> The scanner reached its configured bounded graph limit.</p>}
      <section className="application-map" aria-label="Application nodes grouped by architectural role">
        {groups.map(([group, nodes]) => <article key={group}>
          <header><h2>{group}</h2><span>{nodes.length}</span></header>
          <div>{nodes.slice(0, 18).map((node) => <div className={impacted.has(node.id) ? 'map-node impacted' : 'map-node'} key={node.id} tabIndex={0}>
            <b>{node.label}</b><small>{node.repository}</small><span>{node.type}{node.path ? ` · ${node.path}` : ''}</span>
          </div>)}</div>
          {nodes.length > 18 && <small className="quiet">{nodes.length - 18} additional nodes are available in raw graph data.</small>}
        </article>)}
      </section>
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">ACCESSIBLE EDGE LIST</p><h2>Dependencies and relationships</h2></div><span className="count-pill">{application.graph.edges.length}</span></div>
        <ul className="edge-list">{application.graph.edges.slice(0, 100).map((edge, index) => {
          const source = application.graph.nodes.find((node) => node.id === edge.source)
          const target = application.graph.nodes.find((node) => node.id === edge.target)
          return <li className={impacted.has(edge.source) || impacted.has(edge.target) ? 'impacted' : ''} key={`${edge.source}-${edge.target}-${index}`}><span>{source?.label || edge.source}</span><b>{edge.type}</b><span>{target?.label || edge.target}</span></li>
        })}</ul>
      </article>
    </>}
  </>
}

type AgentAggregate = {
  key: string
  name: string
  provider: string
  model: string
  framework: string
  samples: number
  successes: number
  failures: number
  cost: number | null
  benchmarkStatus: string
  benchmarkValue: string
}

function agentLeaderboard(analytics: RunAnalytics[]): AgentAggregate[] {
  const groups = new Map<string, AgentAggregate>()
  for (const run of analytics) {
    for (const agent of run.agents) {
      const key = [agent.agent_name, agent.provider, agent.model, agent.framework].join('|')
      const current = groups.get(key) || {
        key,
        name: agent.agent_name,
        provider: agent.provider || 'Not recorded',
        model: agent.model || 'Not recorded',
        framework: agent.framework || 'Not recorded',
        samples: 0,
        successes: 0,
        failures: 0,
        cost: null,
        benchmarkStatus: 'NOT_RUN',
        benchmarkValue: 'No authorized benchmark result',
      }
      current.samples += 1
      current.successes += Number(agent.outcome === 'SUCCESS')
      current.failures += Number(agent.outcome === 'FAILURE')
      if (agent.estimated_cost_usd !== null && agent.estimated_cost_usd !== undefined) current.cost = (current.cost || 0) + agent.estimated_cost_usd
      const benchmark = record(agent.details.benchmark)
      if (Object.keys(benchmark).length) {
        current.benchmarkStatus = text(benchmark.status, 'MEASURED')
        current.benchmarkValue = benchmark.score === undefined ? 'Result recorded' : String(benchmark.score)
      }
      groups.set(key, current)
    }
  }
  return [...groups.values()].sort((left, right) => {
    const leftRate = left.samples >= 3 ? left.successes / left.samples : -1
    const rightRate = right.samples >= 3 ? right.successes / right.samples : -1
    return rightRate - leftRate || right.samples - left.samples || left.name.localeCompare(right.name)
  })
}

export function AgentsPage(props: DashboardProps) {
  const leaderboard = agentLeaderboard(props.portfolioAnalytics)
  return <>
    <PageHeader eyebrow="OUTCOME ANALYTICS" title="Agents Leaderboard" description="Measured persisted outcomes only. Small samples and benchmarks that were not run are explicitly labeled." />
    <article className="surface table-surface">
      {leaderboard.length ? <div className="table-wrap"><table><thead><tr><th>Agent</th><th>Outcome measurement</th><th>Samples</th><th>Cost</th><th>Benchmark</th></tr></thead><tbody>{leaderboard.map((agent, index) => {
        const sufficient = agent.samples >= 3
        const rate = sufficient ? Math.round((agent.successes / agent.samples) * 100) : null
        return <tr key={agent.key}><td><span className="rank">{index + 1}</span><b>{agent.name}</b><small>{agent.provider} / {agent.model} · {agent.framework}</small></td><td><Status value={sufficient ? 'MEASURED' : 'INSUFFICIENT SAMPLE'} /><span>{rate === null ? 'No score' : `${rate}% success outcomes`}</span><small>{agent.successes} success · {agent.failures} failure</small></td><td>{agent.samples}</td><td>{money(agent.cost)}</td><td><Status value={agent.benchmarkStatus} /><small>{agent.benchmarkValue}</small></td></tr>
      })}</tbody></table></div> : <MissingData text="No persisted agent_result analytics are available." />}
    </article>
  </>
}

export function PoliciesPage(props: DashboardProps) {
  const plan = props.context?.execution_plan
  return <>
    <PageHeader eyebrow="GOVERNANCE" title="Policies" description="Recorded policy packs, approval gates and autonomy decisions for the selected mission." />
    <section className="two-column">
      <AutonomyDecision props={props} />
      <article className="surface">
        <div className="section-head"><div><p className="eyebrow">POLICY PACK</p><h2>{plan?.policy_pack || 'Not recorded'}</h2></div><Status value={props.analytics?.policy_blocked === true ? 'BLOCKED' : props.analytics?.policy_blocked === false ? 'ALLOWED' : 'NOT RECORDED'} /></div>
        {plan ? <dl className="metadata"><Meta label="Plan revision" value={String(plan.revision)} /><Meta label="Approval gates" value={String(plan.approval_gates.length)} /><Meta label="Pending approvals" value={metric(props.analytics?.pending_approval_count)} /><Meta label="Rejected approvals" value={metric(props.analytics?.rejected_approval_count)} /></dl> : <MissingData text="No execution-plan policy data is available." />}
      </article>
    </section>
    <article className="surface"><div className="section-head"><div><p className="eyebrow">GATES</p><h2>Approval and security controls</h2></div></div>{plan?.approval_gates.length ? plan.approval_gates.map((gate) => <div className="gate-row" key={gate.id}><div><b>{gate.action}</b><p>{gate.reason}</p><small>{gate.gate_type} · {gate.step_ids.length} affected steps</small></div><Status value={gate.status} /></div>) : <MissingData text="No approval gates were recorded." />}</article>
  </>
}

function manifestSection(manifest: Record<string, unknown> | undefined, key: string) {
  return record(manifest?.[key])
}

export function PassportsPage(props: DashboardProps) {
  const { evidenceManifest, evidence, evidenceVerification, supplyChainRecords, supplyChainCompleteness, verifyEvidence, selected, context, analytics } = props
  const delivery = manifestSection(evidenceManifest, 'delivery')
  const task = manifestSection(evidenceManifest, 'task')
  const integrity = manifestSection(evidenceManifest, 'integrity')
  const usage = manifestSection(evidenceManifest, 'usage_cost')
  const exportPassport = () => {
    if (!evidenceManifest) return
    const blob = new Blob([json(evidenceManifest)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `software-change-passport-${selected?.id || 'mission'}.json`
    link.click()
    URL.revokeObjectURL(url)
  }
  const list = (key: string) => Array.isArray(delivery[key]) ? delivery[key].map(String) : []
  return <>
    <PageHeader
      eyebrow="EVIDENCE"
      title="Software Change Passport"
      description="Portable, checksum-backed delivery evidence for the selected mission."
      actions={<><button type="button" onClick={() => void verifyEvidence()} disabled={!evidence.length}>Verify signature</button><button type="button" className="primary" onClick={exportPassport} disabled={!evidenceManifest}>Export JSON</button></>}
    />
    {!evidenceManifest ? <MissingData text="No Evidence Pack manifest is available. Generate evidence for this run before exporting a passport." /> : <>
      <article className="surface passport-hero">
        <div><p className="eyebrow">SOURCE REQUIREMENT</p><h2>{text(task.title, context?.task.title || 'Not recorded')}</h2><p>{text(task.description, context?.task.description || 'Description not recorded')}</p></div>
        <div className="passport-seal"><Status value={evidenceVerification?.status || (record(integrity.signature).present ? 'SIGNED' : 'UNSIGNED')} /><strong>{text(evidenceManifest.schema_version)}</strong><small>{evidence[0]?.manifest_hash || 'Manifest hash not recorded'}</small></div>
      </article>
      {evidenceVerification && <p className={evidenceVerification.valid === false || evidenceVerification.status === 'INVALID' ? 'data-notice danger' : 'data-notice success'}><b>VERIFICATION {evidenceVerification.status}</b> {evidenceVerification.errors?.join('; ') || 'Evidence integrity verification completed.'}</p>}
      <section className="passport-grid">
        <article className="surface"><p className="eyebrow">CHANGESET</p><h2>Commits, diffs and files</h2><h3>Commits</h3><TagList values={list('commit_refs')} empty="No commit references recorded." /><h3>Diff hashes</h3><TagList values={list('diff_hashes')} empty="No diff hashes recorded." /><h3>Changed files</h3><TagList values={list('changed_files')} empty="No changed files recorded." /></article>
        <article className="surface"><p className="eyebrow">VERIFICATION</p><h2>Tests, security and policy</h2><dl className="metadata"><Meta label="Tests" value={metric(analytics?.test_count)} /><Meta label="Verification records" value={metric(list('tests_and_verification').length)} /><Meta label="Security findings" value={metric(analytics?.security_finding_count)} /><Meta label="Policy blocked" value={analytics?.policy_blocked === null || analytics?.policy_blocked === undefined ? 'Not recorded' : analytics.policy_blocked ? 'Yes' : 'No'} /></dl><details><summary>Verification evidence</summary><pre>{json(delivery.tests_and_verification || [])}</pre></details></article>
        <article className="surface"><p className="eyebrow">PROVENANCE</p><h2>Agents, models and cost</h2><div className="agent-stack">{analytics?.agents.map((agent) => <AgentRow agent={agent} key={agent.invocation_id} />)}</div><dl className="metadata"><Meta label="Estimated cost" value={money(analytics?.estimated_cost_usd)} /><Meta label="Manifest cost" value={scalar(usage.estimated_cost_usd || usage.total_estimated_cost_usd)} /></dl></article>
        <article className="surface"><p className="eyebrow">SUPPLY CHAIN</p><h2>Attested evidence</h2><div className="section-head"><Status value={supplyChainCompleteness?.status || 'NOT RECORDED'} /><span>{supplyChainRecords.length} records</span></div><TagList values={supplyChainCompleteness?.present_types} empty="No supply-chain evidence types recorded." />{supplyChainCompleteness?.missing_types.length ? <p className="danger-text">Missing: {supplyChainCompleteness.missing_types.join(', ')}</p> : null}</article>
      </section>
      <article className="surface"><div className="section-head"><div><p className="eyebrow">INTEGRITY</p><h2>Provenance and signature metadata</h2></div></div><pre>{json({ integrity, event_chain: evidenceManifest.event_chain, snapshot: evidenceManifest.snapshot, replay: evidenceManifest.replay })}</pre></article>
    </>}
  </>
}

export function BenchmarksPage(props: DashboardProps) {
  const agents = agentLeaderboard(props.portfolioAnalytics)
  const benchmarked = agents.filter((agent) => agent.benchmarkStatus !== 'NOT_RUN')
  const assessment = props.expertBenchmarkAssessment
  return <>
    <PageHeader eyebrow="EVALUATION" title="Benchmarks" description="Benchmark results are shown only when attached to authorized agent analytics; outcome history is not relabeled as benchmark data." />
    <section className="benchmark-summary"><div><span>Agent configurations</span><strong>{agents.length}</strong></div><div><span>Measured benchmarks</span><strong>{benchmarked.length}</strong></div><div><span>Not run</span><strong>{agents.length - benchmarked.length}</strong></div></section>
    <article className="surface">
      {agents.length ? <div className="benchmark-cards">{agents.map((agent) => <article key={agent.key}><div><b>{agent.name}</b><small>{agent.provider} / {agent.model}</small></div><Status value={agent.benchmarkStatus} /><p>{agent.benchmarkValue}</p><small>Outcome sample: {agent.samples} invocation{agent.samples === 1 ? '' : 's'}</small></article>)}</div> : <MissingData text="No agent configurations are available to benchmark." />}
    </article>
    <ExpertAssessmentTable assessment={assessment} />
  </>
}

function ExpertAssessmentTable({ assessment }: { assessment?: ExpertBenchmarkAssessment }) {
  if (!assessment) {
    return <article className="surface"><MissingData text="No expert assessment has been saved." /></article>
  }
  return <article className="surface table-surface">
    <div className="section-head"><div><p className="eyebrow">EXPERT ASSESSMENT · NOT A BENCHMARK</p><h2>Public capability assessment</h2><p>{assessment.disclaimer}</p></div><Status value="EXPERT OPINION" /></div>
    <div className="table-wrap"><table><thead><tr><th>Product</th><th>Autonomous coding</th><th>Governance</th><th>Vendor-neutral</th><th>Evidence / audit</th><th>UX / maturity</th><th>Overall</th></tr></thead><tbody>{assessment.products.map((product) =>
      <tr key={product.name}><td><b>{product.name}</b></td><td>{product.autonomous_coding.toFixed(1)}</td><td>{product.governance.toFixed(1)}</td><td>{product.vendor_neutral.toFixed(1)}</td><td>{product.evidence_audit.toFixed(1)}</td><td>{product.ux_maturity.toFixed(1)}</td><td><b>{product.overall.toFixed(1)}</b></td></tr>
    )}</tbody></table></div>
    <small>As of {assessment.as_of} · saved {date(assessment.updated_at)} · source: expert opinion</small>
  </article>
}

export function SecurityPage(props: DashboardProps) {
  const findings = props.context?.execution_plan?.security_review?.findings || props.analytics?.details.security_findings || []
  const highCriticalFindings = props.analytics?.high_critical_security_finding_count || 0
  const policyBlocks = props.analytics?.policy_blocked ? 1 : 0
  const actionHeldForReview = highCriticalFindings > 0 || policyBlocks > 0
  return <>
    <PageHeader eyebrow="TRUST CENTER" title="Security" description="Recorded review findings, policy blocks, supply-chain completeness and platform signing health." />
    <section className="security-summary"><div><span>Open findings</span><strong>{metric(props.analytics?.open_security_finding_count)}</strong></div><div><span>High / critical</span><strong>{metric(props.analytics?.high_critical_security_finding_count)}</strong></div><div><span>Policy blocks</span><strong>{policyBlocks}</strong></div><div><span>Signing health</span><Status value={statusOf(props.operationalHealth?.signing)} /></div><div><span>Supply chain</span><Status value={props.supplyChainCompleteness?.status || 'NOT RECORDED'} /></div></section>
    <section className="two-column">
      <article className="surface"><div className="section-head"><div><p className="eyebrow">SECURITY REVIEW</p><h2>{props.context?.execution_plan?.security_review?.status || 'Not recorded'}</h2></div></div>{findings.length ? findings.map((finding, index) => <div className="finding" key={text(finding.finding_id, String(index))}><Status value={text(finding.severity, 'UNKNOWN')} /><div><b>{text(finding.title, 'Untitled finding')}</b><p>{text(finding.description, 'Description not recorded.')}</p></div></div>) : <MissingData text="No security findings were persisted." />}</article>
      <article className="surface"><div className="section-head"><div><p className="eyebrow">SECURITY AUTONOMY</p><h2>{actionHeldForReview ? 'Action held for review' : 'No recorded policy block'}</h2></div><Status value={actionHeldForReview ? 'REVIEW REQUIRED' : 'SUGGESTED'} /></div><p>{actionHeldForReview ? 'Recorded policy blocks or high/critical findings require remediation or approval before relying on an autonomous action.' : 'This is not a security guarantee; it means no blocking signal was returned by the selected mission APIs.'}</p><details><summary>Security evidence</summary><pre>{json({ review: props.context?.execution_plan?.security_review, supply_chain: props.supplyChainCompleteness, signing: props.operationalHealth?.signing })}</pre></details></article>
    </section>
  </>
}

export function SettingsPage(props: DashboardProps) {
  const submit = (event: FormEvent) => props.submitSettings(event)
  return <>
    <PageHeader eyebrow="ADMINISTRATION" title="Settings" description="API connection and authentication are configured only here and stored locally where indicated." />
    <section className="settings-layout">
      <form className="surface settings-form" onSubmit={submit}>
        <div><p className="eyebrow">CONNECTION</p><h2>SACM API</h2></div>
        <label htmlFor="api-url">API URL<span>Reverse-proxy path or absolute SACM API origin.</span><input id="api-url" value={props.baseUrl} onChange={(event) => props.setBaseUrl(event.target.value)} autoComplete="url" /></label>
        <label htmlFor="actor">Actor<span>Authenticated actor or service identity presented to SACM.</span><input id="actor" value={props.actor} onChange={(event) => props.setActor(event.target.value)} required autoComplete="username" /></label>
        <label htmlFor="token">Access token<span>Held in memory only; it is not written to localStorage.</span><input id="token" value={props.token} onChange={(event) => props.setToken(event.target.value)} type="password" autoComplete="current-password" placeholder="Optional in local development" /></label>
        <button type="submit" className="primary">Connect and refresh</button>
      </form>
      <article className="surface"><p className="eyebrow">CONNECTION STATE</p><h2>Current workspace</h2><dl className="metadata"><Meta label="API" value={props.baseUrl} /><Meta label="Actor" value={props.actor} /><Meta label="Authentication" value={props.token ? 'Bearer token in memory' : 'Actor header only'} /><Meta label="Authorized organizations" value={String(props.clients.length)} /></dl><p className="quiet">Authorization remains enforced by every backend resource endpoint. Mission Control hides unavailable optional operational data rather than treating it as healthy.</p></article>
    </section>
  </>
}
