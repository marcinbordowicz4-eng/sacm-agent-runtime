import { useEffect, useMemo, useRef, useState } from 'react'
import type { DashboardProps, Run } from '../types'
import {
  AgentsPage,
  ApplicationsPage,
  BenchmarksPage,
  CommandCenterPage,
  MissionsPage,
  PassportsPage,
  PoliciesPage,
  SecurityPage,
  SettingsPage,
} from './MissionPages'
import { NavigationRail } from './MissionNavigation'
import { navigationItems, type DashboardView } from './viewTypes'

type CommandItem = {
  id: string
  label: string
  description: string
  keywords: string
  action: () => void
}

export function MissionControl(props: DashboardProps) {
  const [activeView, setActiveView] = useState<DashboardView>('command')
  const [commandOpen, setCommandOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const attentionCount = props.portfolioAnalytics.reduce(
    (sum, item) => sum + Number(item.policy_blocked === true) + (item.high_critical_security_finding_count || 0) + item.pending_approval_count,
    0,
  )

  const navigate = (view: DashboardView) => {
    setActiveView(view)
    setCommandOpen(false)
    setQuery('')
    document.querySelector<HTMLElement>('#page-content')?.focus()
  }

  const selectMission = (run: Run) => {
    void props.loadRun(run)
    navigate('missions')
  }

  const commands = useMemo<CommandItem[]>(() => [
    ...navigationItems.map((item) => ({
      id: `view-${item.id}`,
      label: `Open ${item.label}`,
      description: 'Navigate within Mission Control',
      keywords: `${item.label} navigate page`,
      action: () => navigate(item.id),
    })),
    {
      id: 'view-settings',
      label: 'Open Settings',
      description: 'Configure API URL, actor and access token',
      keywords: 'settings api connection auth token actor',
      action: () => navigate('settings'),
    },
    {
      id: 'refresh',
      label: 'Refresh authorized telemetry',
      description: 'Reload runs, analytics and selected mission data',
      keywords: 'refresh reload sync',
      action: () => {
        void props.loadRuns()
        setCommandOpen(false)
      },
    },
    ...props.runs.map((run) => ({
      id: `mission-${run.id}`,
      label: `Open mission ${run.task_id.slice(0, 10)}`,
      description: `${run.status} · updated ${new Date(run.updated_at).toLocaleString()}`,
      keywords: `${run.task_id} ${run.id} ${run.status} mission`,
      action: () => selectMission(run),
    })),
  // Functions intentionally capture the latest dashboard state.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  ], [props.runs, props.loadRuns, props.loadRun])

  const results = query.trim()
    ? commands.filter((item) => `${item.label} ${item.description} ${item.keywords}`.toLowerCase().includes(query.toLowerCase())).slice(0, 12)
    : commands.slice(0, 9)

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandOpen((open) => !open)
      }
      if (event.key === 'Escape') setCommandOpen(false)
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  }, [])

  useEffect(() => {
    if (commandOpen) requestAnimationFrame(() => searchRef.current?.focus())
  }, [commandOpen])

  const page = {
    command: <CommandCenterPage {...props} onMission={selectMission} />,
    missions: <MissionsPage {...props} />,
    applications: <ApplicationsPage {...props} />,
    agents: <AgentsPage {...props} />,
    policies: <PoliciesPage {...props} />,
    passports: <PassportsPage {...props} />,
    benchmarks: <BenchmarksPage {...props} />,
    security: <SecurityPage {...props} />,
    settings: <SettingsPage {...props} />,
  }[activeView]

  return <main className="workspace">
    <a className="skip-link" href="#page-content">Skip to content</a>
    <NavigationRail activeView={activeView} onNavigate={navigate} attentionCount={attentionCount} />
    <section className="mission-shell">
      <div className="topbar">
        <button type="button" className="global-search" onClick={() => setCommandOpen(true)} aria-haspopup="dialog">
          <span aria-hidden="true">⌕</span><b>Search missions, applications and views</b><kbd>⌘ K</kbd>
        </button>
        <div className="topbar-state"><span className={props.error ? 'connection-dot error-dot' : 'connection-dot'} /><span>{props.error ? 'Connection issue' : props.loading ? 'Refreshing' : 'API connected'}</span></div>
      </div>
      {props.error && <p className="error" role="alert">{props.error}</p>}
      <div id="page-content" className="page-content" tabIndex={-1} aria-live="polite">{page}</div>
    </section>
    {commandOpen && <div className="command-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) setCommandOpen(false)
    }}>
      <section className="command-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title">
        <header><span aria-hidden="true">⌕</span><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Navigate, filter missions, or run a safe action" aria-label="Mission Control command search" /><kbd>Esc</kbd></header>
        <h2 id="command-title">Mission Control commands</h2>
        <p>Navigation and safe application actions only. Arbitrary shell commands are never executed.</p>
        <div className="command-results">{results.length ? results.map((item) => <button type="button" key={item.id} onClick={item.action}><span><b>{item.label}</b><small>{item.description}</small></span><i aria-hidden="true">↵</i></button>) : <p className="missing">No matching view, mission or safe action.</p>}</div>
      </section>
    </div>}
  </main>
}
