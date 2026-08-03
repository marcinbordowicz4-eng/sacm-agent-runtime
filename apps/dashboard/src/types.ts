import type { Dispatch, FormEvent, SetStateAction } from 'react'

export type Run = {
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

export type Step = {
  id: string
  sequence: number
  name: string
  status: string
  retry_count: number
  started_at?: string | null
  completed_at?: string | null
}

export type Event = {
  id: string
  sequence: number
  event_type: string
  actor: string
  payload: Record<string, unknown>
  occurred_at: string
}

export type Approval = {
  id: string
  action: string
  status: string
  requested_at: string
  decided_at?: string | null
  resource: Record<string, unknown>
}

export type Evidence = {
  id: string
  path: string
  manifest_hash: string
  created_at: string
}

export type Snapshot = {
  id: string
  event_sequence: number
  checksum: string
  creation_reason: string
  created_at: string
}

export type Client = {
  id: string
  slug: string
  name: string
  projects: {
    id: string
    name: string
    repository_full_name?: string | null
  }[]
}

export type AgentConfiguration = {
  agent_name?: string
  role?: string
  runtime_kind?: string
  implementation_ref?: string
  configuration?: Record<string, unknown>
}

export type PlanStep = {
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

export type ExecutionPlan = {
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
  approval_gates: {
    id: string
    gate_type: string
    action: string
    reason: string
    status: string
    step_ids: string[]
    approval_id?: string | null
  }[]
  steps: PlanStep[]
}

export type ApplicationGraphNode = {
  id: string
  type: string
  repository: string
  label: string
  path?: string | null
  metadata: Record<string, unknown>
}

export type ApplicationGraphEdge = {
  source: string
  target: string
  type: string
}

export type ApplicationContextFull = {
  id: string
  task_id: string
  schema_version: string
  status: string
  scanner_version: string
  graph: {
    nodes: ApplicationGraphNode[]
    edges: ApplicationGraphEdge[]
    truncated: boolean
    limits: Record<string, number>
  }
  graph_hash: string
  impact_analysis: {
    query_terms: string[]
    impacted_nodes: {
      node_id: string
      score: number
      matched_terms: string[]
      reasons: string[]
    }[]
    impacted_repository_count: number
    truncated: boolean
  }
  risk_analysis: {
    score: number
    level: string
    factors: {
      code: string
      contribution: number
      explanation: string
    }[]
  }
  repositories: {
    position: number
    full_name?: string | null
    requested_path?: string | null
    resolved_path?: string | null
    base_revision?: string | null
    status: string
    error_code?: string | null
    error_message?: string | null
    file_count: number
    skipped_file_count: number
    scan_metadata?: Record<string, unknown>
  }[]
  created_at: string
  updated_at: string
}

export type RunContext = {
  run: {
    id: string
    workflow_version: string
    source_revision?: string | null
    target_repo_path?: string | null
  }
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
    clarifications?: {
      id: string
      field_name: string
      question: string
      status: string
      answer?: unknown
      created_at: string
      answered_at?: string | null
    }[]
    created_at: string
    updated_at: string
  }
  organization?: { id: string; slug: string; name: string } | null
  project?: {
    id: string
    slug: string
    name: string
    repository_full_name?: string | null
    repository_path?: string | null
  } | null
  application_context?: {
    id: string
    status: string
    scanner_version: string
    graph_hash: string
    impact_analysis: Record<string, unknown>
    risk_analysis: Record<string, unknown>
    repositories: {
      position: number
      full_name?: string | null
      requested_path?: string | null
      resolved_path?: string | null
      base_revision?: string | null
      status: string
      error_code?: string | null
      error_message?: string | null
      file_count: number
      skipped_file_count: number
    }[]
  } | null
  execution_plan?: ExecutionPlan | null
  jira_delivery?: {
    status: string
    jira_status?: string | null
    status_comment_id?: string | null
    pr_status: string
    pr_url?: string | null
    context: Record<string, unknown>
    last_error?: string | null
    updated_at: string
  } | null
  costs: Record<string, unknown>
}

export type OutcomeStep = {
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

export type OutcomeAgent = {
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

export type RunAnalytics = {
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

export type AggregateAnalytics = {
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

export type ReplayComparison = {
  source_run_id: string
  replay_run_id: string
  source_snapshot_id: string
  [key: string]: unknown
}

export type OperationalHealth = {
  status: string
  generated_at: string
  checks: Record<string, unknown>
  queue: Record<string, unknown>
  executors: Record<string, unknown>
  backup: Record<string, unknown>
  audit: Record<string, unknown>
  governance: Record<string, unknown>
  signing: Record<string, unknown>
}

export type ExecutorFleetHealth = {
  scope_key: string
  total: number
  active: number
  offline: number
  revoked: number
  draining: number
  capacity: {
    max_concurrent_jobs: number
    active_jobs: number
    available_slots: number
  }
  slo: Record<string, unknown>
}

export type ExecutionJob = {
  id: string
  run_id: string
  run_step_id: string
  task_id: string
  state: string
  executor_id?: string | null
  attempt_count?: number
  created_at?: string
  updated_at?: string
}

export type SupplyChainRecord = {
  id: string
  schema_version: string
  run_id: string
  record_type: string
  format: string
  subject_name: string
  subject_digest: string
  artifact_sha256: string
  status: string
  coverage: Record<string, unknown>
  content: Record<string, unknown>
  metadata: Record<string, unknown>
  artifact_id?: string | null
  evidence_pack_id?: string | null
  image_id?: string | null
  release_id?: string | null
  created_at: string
}

export type SupplyChainCompleteness = {
  schema_version: string
  status: 'COMPLETE' | 'INCOMPLETE'
  mandatory_types: string[]
  present_types: string[]
  missing_types: string[]
}

export type EvidenceVerification = {
  status: string
  valid?: boolean
  chain_valid?: boolean
  manifest_hash?: string
  errors?: string[]
  [key: string]: unknown
}

export type WorkflowProgressEntry = {
  event_id: string
  phase: string
  status: string
  agent?: string | null
  step?: number | null
  elapsed_ms: number
  created_at: string
  details: Record<string, unknown>
}

export type WorkflowProgress = {
  schema_version: 'workflow-progress-status/v1'
  task_id: string
  task_status: string
  state: 'running' | 'stalled' | 'finished' | 'failed'
  lease_active: boolean
  phase?: string | null
  agent?: string | null
  step?: number | null
  elapsed_ms: number
  last_update?: string | null
  entries: WorkflowProgressEntry[]
}

export type LifecycleMetrics = {
  schema_version: 'lifecycle-metrics/v1'
  run_id: string
  metrics: {
    metric: string
    count: number
    sum: number
    average: number
    maximum: number
  }[]
  telemetry?: {
    task_id: string
    usage: Record<string, unknown>[]
    input_tokens: number
    output_tokens: number
    estimated_cost_usd: number
    cost_estimation_available: boolean
    tool_execution_count: number
    tool_duration_ms: number
    failed_tool_execution_count: number
  } | null
}

export type DashboardProps = {
  baseUrl: string
  setBaseUrl: Dispatch<SetStateAction<string>>
  actor: string
  setActor: Dispatch<SetStateAction<string>>
  token: string
  setToken: Dispatch<SetStateAction<string>>
  runs: Run[]
  selected?: Run
  steps: Step[]
  events: Event[]
  approvals: Approval[]
  evidence: Evidence[]
  snapshots: Snapshot[]
  clients: Client[]
  context?: RunContext
  analytics?: RunAnalytics
  projectAnalytics?: AggregateAnalytics
  organizationAnalytics?: AggregateAnalytics
  comparison?: ReplayComparison
  portfolioAnalytics: RunAnalytics[]
  fullApplication?: ApplicationContextFull
  operationalHealth?: OperationalHealth
  executorFleet?: ExecutorFleetHealth
  executionJobs: ExecutionJob[]
  supplyChainRecords: SupplyChainRecord[]
  supplyChainCompleteness?: SupplyChainCompleteness
  evidenceManifest?: Record<string, unknown>
  evidenceVerification?: EvidenceVerification
  lifecycleMetrics?: LifecycleMetrics
  progress?: WorkflowProgress
  progressError: string
  error: string
  loading: boolean
  loadRuns: () => Promise<void>
  loadRun: (run: Run) => Promise<void>
  action: (path: string, body?: Record<string, unknown>) => Promise<void>
  verifyEvidence: () => Promise<void>
  submitSettings: (event: FormEvent) => void
}
