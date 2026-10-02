import { useState, type FormEvent } from 'react'
import type { Client, MissionCreateInput } from '../types'

type MissionComposerProps = {
  clients: Client[]
  loading: boolean
  onClose: () => void
  onCreate: (input: MissionCreateInput) => Promise<void>
}

export function MissionComposer({ clients, loading, onClose, onCreate }: MissionComposerProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [projectId, setProjectId] = useState('')
  const [repoPath, setRepoPath] = useState('')
  const [revision, setRevision] = useState('')
  const [startImmediately, setStartImmediately] = useState(true)
  const [error, setError] = useState('')

  const projects = clients.flatMap((client) => client.projects.map((project) => ({
    ...project,
    organization: client.name,
  })))

  const selectProject = (id: string) => {
    setProjectId(id)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    try {
      await onCreate({
        title: title.trim(),
        description: description.trim(),
        project_id: projectId || undefined,
        target_repo_path: repoPath.trim() || undefined,
        source_revision: revision.trim() || undefined,
        startImmediately,
      })
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Mission could not be created.')
    }
  }

  return <div className="composer-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.currentTarget === event.target && !loading) onClose()
  }}>
    <section className="mission-composer" role="dialog" aria-modal="true" aria-labelledby="new-mission-title">
      <header><div><p className="eyebrow">NEW MISSION</p><h2 id="new-mission-title">Send governed work to an agent</h2><p>Every mission receives a durable plan, policy decision and evidence trail.</p></div><button type="button" aria-label="Close mission composer" onClick={onClose} disabled={loading}>×</button></header>
      <form onSubmit={(event) => void submit(event)}>
        <label>Outcome to deliver<input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={255} placeholder="e.g. Add idempotent refunds" autoFocus /></label>
        <label>Brief and acceptance criteria<textarea value={description} onChange={(event) => setDescription(event.target.value)} required placeholder="Describe the change, constraints, acceptance criteria and verification expected." /></label>
        <div className="composer-grid">
          <label>Authorized project<select value={projectId} onChange={(event) => selectProject(event.target.value)}><option value="">Unlinked / local development</option>{projects.map((project) => <option value={project.id} key={project.id}>{project.organization} · {project.name}</option>)}</select><small>Production runs require a project authorized for your actor.</small></label>
          <label>Base revision<input value={revision} onChange={(event) => setRevision(event.target.value)} placeholder="main or a commit SHA" /><small>Optional; use this to make scope reproducible.</small></label>
        </div>
        <label>Repository path<input value={repoPath} onChange={(event) => setRepoPath(event.target.value)} placeholder="/workspace/payments or acme/payments" /><small>Must match the selected project in production. The executor receives this as its scoped repository.</small></label>
        <label className="start-option"><input type="checkbox" checked={startImmediately} onChange={(event) => setStartImmediately(event.target.checked)} /> <span><b>Start execution now</b><small>Otherwise the mission is created and remains ready for an explicit execution request.</small></span></label>
        {error && <p className="data-notice danger" role="alert">{error}</p>}
        <footer><button type="button" onClick={onClose} disabled={loading}>Cancel</button><button type="submit" className="primary" disabled={loading}>{loading ? 'Creating mission…' : startImmediately ? 'Create and start mission' : 'Create mission'}</button></footer>
      </form>
    </section>
  </div>
}
