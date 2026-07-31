export type DashboardView =
  | 'command'
  | 'missions'
  | 'applications'
  | 'agents'
  | 'policies'
  | 'passports'
  | 'benchmarks'
  | 'security'
  | 'settings'

export const navigationItems: { id: DashboardView; label: string; short: string }[] = [
  { id: 'command', label: 'Command Center', short: '⌂' },
  { id: 'missions', label: 'Missions', short: 'M' },
  { id: 'applications', label: 'Applications', short: 'A' },
  { id: 'agents', label: 'Agents', short: 'G' },
  { id: 'policies', label: 'Policies', short: 'P' },
  { id: 'passports', label: 'Evidence & Passports', short: 'E' },
  { id: 'benchmarks', label: 'Benchmarks', short: 'B' },
  { id: 'security', label: 'Security', short: 'S' },
]
