import { navigationItems, type DashboardView } from './viewTypes'

type NavigationRailProps = {
  activeView: DashboardView
  onNavigate: (view: DashboardView) => void
  attentionCount: number
}

export function NavigationRail({ activeView, onNavigate, attentionCount }: NavigationRailProps) {
  return <nav className="rail" aria-label="Mission Control">
    <button type="button" className="brand-mark" onClick={() => onNavigate('command')} aria-label="SACM Command Center">S</button>
    <div className="rail-links">
      {navigationItems.map((item) => <button
        type="button"
        key={item.id}
        className={activeView === item.id ? 'rail-link active' : 'rail-link'}
        onClick={() => onNavigate(item.id)}
        aria-current={activeView === item.id ? 'page' : undefined}
        title={item.label}
      >
        <span aria-hidden="true">{item.short}</span>
        <small>{item.label}</small>
        {item.id === 'security' && attentionCount > 0 && <i aria-label={`${attentionCount} actions require attention`}>{attentionCount}</i>}
      </button>)}
    </div>
    <button
      type="button"
      className={activeView === 'settings' ? 'rail-link active settings-link' : 'rail-link settings-link'}
      onClick={() => onNavigate('settings')}
      aria-current={activeView === 'settings' ? 'page' : undefined}
      title="Settings"
    >
      <span aria-hidden="true">⚙</span><small>Settings</small>
    </button>
  </nav>
}
