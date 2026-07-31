
import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import './App.css'

type Run = {
  id: string
  task_id: string
  status: string
  source_revision?: string | null
  target_repo_path?: string | null
  created_at: string
  updated_at: string
}
type Step = { id: string; name: string; status: string; retry_count: number; completed_at?: string | null }
type Event = { id: string; sequence: number; event_type: string; actor: string; payload: Record<string, unknown>; occurred_at: string }
type Approval = { id: string; action: string; status: string; requested_at: string; resource: Record<string, unknown> }
type Evidence = { id: string; path: string; manifest_hash: string; created_at: string }
type Client = { id: string; slug: string; name: string; projects: { id: string; name: string; repository_full_name?: string | null }[] }
type AgentInvocation = {
  event_id: string
  name: string
  role?: string | null
  status?: string | null
  summary?: string | null
  confidence?: number | null
  next_state_hint?: string | null
  tool_execution: Record<string, unknown>[]
  created_at: string
}
type RunContext = {
  run: { id: string; workflow_version: string; source_revision?: string | null; target_repo_path?: string | null }
  task: { id: string; title: string; description: string; status: string; target_repo_path?: string | null; created_at: string; updated_at: string }
  organization?: { id: string; slug: string; name: string } | null
  project?: { id: string; slug: string; name: string; repository_full_name?: string | null; repository_path?: string | null } | null
  agents: AgentInvocation[]
  costs: Record<string, unknown>
}

const json = (value: unknown) => JSON.stringify(value, null, 2)
const date = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'

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
  const [costs, setCosts] = useState<Record<string, unknown>>({})
  const [clients, setClients] = useState<Client[]>([])
  const [context, setContext] = useState<RunContext>()
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

  const loadRun = async (run: Run) => {
    setLoading(true)
    setError('')
    try {
      const [current, nextContext, nextSteps, nextEvents, nextApprovals, nextEvidence] = await Promise.all([
        request<Run>(`/v1/runs/${run.id}`),
        request<RunContext>(`/v1/runs/${run.id}/context`),
        request<Step[]>(`/v1/runs/${run.id}/steps`),
        request<Event[]>(`/v1/runs/${run.id}/events`),
        request<Approval[]>(`/v1/approvals?run_id=${run.id}`),
        request<Evidence[]>(`/v1/runs/${run.id}/evidence`),
      ])
      setSelected(current); setContext(nextContext); setSteps(nextSteps); setEvents(nextEvents); setApprovals(nextApprovals); setEvidence(nextEvidence); setCosts(nextContext.costs)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load run') }
    finally { setLoading(false) }
  }

  const loadRuns = async () => {
    setLoading(true); setError('')
    try {
      const nextRuns = await request<Run[]>('/v1/runs')
      setRuns(nextRuns)
      const current = selected && nextRuns.find((run) => run.id === selected.id)
      if (current) await loadRun(current)
      else if (nextRuns[0]) await loadRun(nextRuns[0])
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load runs') }
    finally { setLoading(false) }
  }

  const loadClients = async () => {
    const organizations = await request<Omit<Client, 'projects'>[]>('/v1/organizations')
    const populated = await Promise.all(organizations.map(async (organization) => ({
      ...organization, projects: await request<Client['projects']>(`/v1/organizations/${organization.id}/projects`),
    })))
    setClients(populated)
  }

  // Initial connection load; later refreshes are explicit to avoid request loops.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void Promise.all([loadRuns(), loadClients()]).catch((cause) => setError(cause instanceof Error ? cause.message : 'Unable to load operational data')) }, [])

  const failures = events.filter((event) => /failed|error/i.test(event.event_type) || event.payload.failure)
  const agents = context?.agents || []
  const commands = agents.flatMap((agent) => agent.tool_execution.map((item) => ({ agent, item })))

  const action = async (path: string, body?: Record<string, unknown>) => {
    if (!selected) return
    try { await request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }); await loadRun(selected) }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Action failed') }
  }

  const submitSettings = (event: FormEvent) => { event.preventDefault(); localStorage.setItem('sacm-actor', actor); void loadRuns() }

  return <main>
    <header>
      <div><p className="eyebrow">SACM CONTROL PLANE</p><h1>Delivery operations</h1></div>
      <button className="primary" onClick={() => void loadRuns()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh data'}</button>
    </header>
    <form className="connection" onSubmit={submitSettings}>
      <label>API URL<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
      <label>Actor<input value={actor} onChange={(e) => setActor(e.target.value)} required /></label>
      <label>Bearer token <input value={token} onChange={(e) => setToken(e.target.value)} type="password" placeholder="Optional for local mode" /></label>
      <button>Connect</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    <section className="layout">
      <aside>
        <h2>Runs <span>{runs.length}</span></h2>
        <div className="run-list">{runs.map((run) => <button key={run.id} className={selected?.id === run.id ? 'run active' : 'run'} onClick={() => void loadRun(run)}>
          <strong>{run.status}</strong><span>{run.id.slice(0, 8)}</span><small>{date(run.updated_at)}</small>
        </button>)}</div>
        <h2 className="clients-heading">Clients <span>{clients.length}</span></h2>
        <div className="clients">{clients.map((client) => <div key={client.id}><b>{client.name}</b><small>{client.projects.map((project) => project.repository_full_name || project.name).join(', ') || 'No projects'}</small></div>)}</div>
      </aside>
      <section className="content">
        {!selected ? <div className="empty">No authorized runs available.</div> : <>
          <div className="run-title"><div><p className="eyebrow">RUN {selected.id}</p><h2>{selected.status}</h2><p>{selected.target_repo_path || 'No repository attached'}</p></div>
            <div className="actions"><button onClick={() => void action(`/v1/runs/${selected.id}/cancel`)}>Cancel</button><button onClick={() => void action(`/v1/runs/${selected.id}/resume`)}>Resume</button></div></div>
          <section className="metrics">
            <Metric label="Agents" value={agents.length} /><Metric label="Steps" value={steps.length} /><Metric label="Approvals" value={approvals.filter((item) => item.status === 'PENDING').length} /><Metric label="Failures" value={failures.length} />
          </section>
          <section className="grid">
            <Panel title="Client & project">{context?.organization ? <dl className="metadata">
              <div><dt>Client</dt><dd>{context.organization.name}</dd></div>
              <div><dt>Organization ID</dt><dd>{context.organization.id}</dd></div>
              <div><dt>Project</dt><dd>{context.project?.name || '—'}</dd></div>
              <div><dt>Repository</dt><dd>{context.project?.repository_full_name || context.project?.repository_path || '—'}</dd></div>
            </dl> : <p>This legacy run is not linked to a client or project.</p>}</Panel>
            <Panel title="Processed task">{context ? <dl className="metadata">
              <div><dt>Title</dt><dd>{context.task.title}</dd></div>
              <div><dt>Status</dt><dd>{context.task.status}</dd></div>
              <div><dt>Task ID</dt><dd>{context.task.id}</dd></div>
              <div><dt>Workflow</dt><dd>{context.run.workflow_version}</dd></div>
              <div><dt>Source revision</dt><dd>{context.run.source_revision || 'Not recorded'}</dd></div>
              <div className="wide"><dt>Description</dt><dd>{context.task.description}</dd></div>
            </dl> : <p>Task metadata unavailable.</p>}</Panel>
            <Panel title="Timeline"><ol className="timeline">{events.map((event) => <li key={event.id}><b>{event.event_type}</b><span>{event.actor} · {date(event.occurred_at)}</span><code>{json(event.payload)}</code></li>)}</ol></Panel>
            <Panel title="Invoked agents">{agents.length ? <div>{agents.map((agent) => <div className="agent" key={agent.event_id}>
              <div><b>{agent.name}</b><span>{agent.role || 'unknown role'} · {agent.status || 'status unavailable'} · {date(agent.created_at)}</span></div>
              <p>{agent.summary || 'No summary recorded.'}</p>
              <small>Confidence: {agent.confidence ?? '—'} · Next: {agent.next_state_hint || '—'}</small>
            </div>)}</div> : <p>No agent invocation was recorded before this run ended.</p>}</Panel>
            <Panel title="Approvals">{approvals.map((approval) => <div className="approval" key={approval.id}><b>{approval.action}</b><span>{approval.status}</span>{approval.status === 'PENDING' && <div><button onClick={() => void action(`/v1/approvals/${approval.id}/decision`, { approve: true, reason: 'Approved from SACM dashboard.' })}>Approve</button><button onClick={() => void action(`/v1/approvals/${approval.id}/decision`, { approve: false, reason: 'Rejected from SACM dashboard.' })}>Reject</button></div>}</div>) || <p>No approvals.</p>}</Panel>
            <Panel title="Evidence">{evidence.map((item) => <div className="evidence" key={item.id}><b>{item.manifest_hash.slice(0, 16)}…</b><span>{item.path}</span></div>) || <p>No evidence package yet.</p>}</Panel>
            <Panel title="Steps">{steps.map((step) => <div className="step" key={step.id}><span>{step.name}</span><b>{step.status}</b>{step.status === 'FAILED' && <button onClick={() => void action(`/v1/runs/${selected.id}/steps/${step.id}/retry`)}>Retry</button>}</div>)}</Panel>
            <Panel title="Cost & tool usage"><pre>{json(costs)}</pre></Panel>
            <Panel title="Executed commands">{commands.length ? commands.map(({ agent, item }, index) => <pre key={`${agent.event_id}-${index}`}>{agent.name}{'\n'}{json(item)}</pre>) : <p>No command evidence recorded.</p>}</Panel>
            <Panel title="Failure reason">{failures.length ? failures.map((event) => <pre key={event.id}>{json(event.payload)}</pre>) : <p>No failure recorded.</p>}</Panel>
          </section>
        </>}
      </section>
    </section>
  </main>
}

function Metric({ label, value }: { label: string; value: number }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div> }
function Panel({ title, children }: { title: string; children: ReactNode }) { return <article><h3>{title}</h3>{children}</article> }
export default App
