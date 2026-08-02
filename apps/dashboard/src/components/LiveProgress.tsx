import { date, duration } from '../formatters'
import type { WorkflowProgress } from '../types'
import { MissingData, Status } from './DashboardPrimitives'

export function LiveProgress({ progress, error }: { progress?: WorkflowProgress; error: string }) {
  return <article className="surface live-progress" aria-live="polite">
    <div className="section-head">
      <div><p className="eyebrow">LIVE PROGRESS</p><h2>{progress?.agent || progress?.phase || 'Waiting for activity'}</h2></div>
      <Status value={progress?.state || 'not recorded'} />
    </div>
    {error
      ? <p className="data-notice danger">{error}</p>
      : !progress
        ? <MissingData text="No workflow progress has been recorded for this task." />
        : <>
            <dl className="metadata compact">
              <div><dt>Task status</dt><dd>{progress.task_status}</dd></div>
              <div><dt>Lease</dt><dd>{progress.lease_active ? 'Active' : 'Inactive'}</dd></div>
              <div><dt>Current step</dt><dd>{progress.step ?? 'Not recorded'}</dd></div>
              <div><dt>Elapsed</dt><dd>{duration(progress.elapsed_ms)}</dd></div>
            </dl>
            <p className="progress-updated">Last update {date(progress.last_update)}</p>
            {progress.entries.length
              ? <ol className="progress-entries">{progress.entries.map((entry) =>
                  <li key={entry.event_id}>
                    <span>{entry.step ?? '—'}</span>
                    <div><b>{entry.agent || entry.phase}</b><small>{entry.status.replaceAll('_', ' ')} · {duration(entry.elapsed_ms)}</small></div>
                    <time dateTime={entry.created_at}>{date(entry.created_at)}</time>
                  </li>,
                )}</ol>
              : <MissingData text="The workflow has not emitted progress entries yet." />}
          </>}
  </article>
}
