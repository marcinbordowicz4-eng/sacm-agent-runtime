import type { ReactNode } from 'react'
import { duration, json, metric, money } from '../formatters'
import type { AggregateAnalytics } from '../types'

export function DetailPanel({ title, children, wide = false }: { title: string; children: ReactNode; wide?: boolean }) {
  return <article className={wide ? 'detail-panel wide' : 'detail-panel'}><h3>{title}</h3>{children}</article>
}

export function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>
}

export function Panel({ title, children, wide = false }: { title: string; children: ReactNode; wide?: boolean }) {
  return <article className={wide ? 'wide-panel' : undefined}><h3>{title}</h3>{children}</article>
}

export function Meta({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>
}

export function Status({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll('_', '-')
  return <span className={`status ${normalized}`}>{value}</span>
}

export function MissingData({ text: value }: { text: string }) {
  return <p className="missing">{value}</p>
}

export function TagList({ values, empty }: { values?: string[]; empty: string }) {
  return values?.length
    ? <div className="tags">{values.map((value) => <span key={value}>{value}</span>)}</div>
    : <MissingData text={empty} />
}

export function KeyValues({ value }: { value: Record<string, unknown> }) {
  return <dl className="metadata compact">{Object.entries(value).map(([key, item]) =>
    <Meta
      key={key}
      label={key.replaceAll('_', ' ')}
      value={typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? String(item) : json(item)}
    />,
  )}</dl>
}

export function Aggregate({ title, value }: { title: string; value?: AggregateAnalytics }) {
  return <div className="aggregate">
    <h4>{title}</h4>
    {value
      ? <dl>
          <Meta label="Runs" value={metric(value.run_count)} />
          <Meta label="Success rate" value={metric(value.success_rate_percent, '%')} />
          <Meta label="Average latency" value={duration(value.average_latency_ms)} />
          <Meta label="Estimated cost" value={money(value.estimated_cost_usd)} />
          <Meta label="Retries" value={metric(value.retry_count)} />
          <Meta label="Legacy / incomplete" value={`${value.legacy_run_count} / ${value.incomplete_run_count}`} />
        </dl>
      : <MissingData text="No authorized aggregate is available." />}
  </div>
}
