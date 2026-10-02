# Competitive gap analysis

_Assessed 2026-10-02 using public vendor documentation. Product marketing is
not treated as evidence that a private UI or deployment was observed._

| Scenario | Required result | Benchmark and source | SACM state and repository evidence | Gap | Priority | Cost / risk | Acceptance criterion |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Durable agent work | Work survives worker interruption with inspectable history | LangGraph documents persistence/checkpoint patterns; Kubiya documents durable queued work across workers ([Kubiya Agents](https://docs.kubiya.ai/core-concepts/agents)) | Runs, queue leases, recovery and snapshots exist in `run_service.py`, `workflow_queue_service.py`, `execution_plane_service.py` | Staging recovery evidence missing | P0 | Duplicate effects after recovery | Restart a worker during a run; one durable history and no duplicate external delivery. |
| Trace and cost diagnosis | Operators can inspect duration, tokens, cost and quality by execution | Langfuse documents traces, cost/latency and evaluation dashboards ([observability](https://langfuse.com/docs/observability/overview), [metrics](https://langfuse.com/docs/metrics/overview)) | Lifecycle metrics and analytics APIs plus dashboard telemetry | No trace-level log search or evaluated acceptance metric | P1 | Cost and quality blind spots | A run detail links every recorded tool/model result to cost, latency and evidence; unavailable fields remain unavailable. |
| Governed execution | Agent inherits scoped tools, model, environment and policy | Kubiya documents environment/policy inheritance and worker queues ([Agents](https://docs.kubiya.ai/core-concepts/agents)) | Agent contracts, execution plans, credential leases and policy service | UI cannot yet configure a complete reusable agent/environment catalog | P1 | Misconfiguration and privilege sprawl | Authorized user can inspect agent capabilities, executor boundary and applicable policy for every run. |
| Issue to reviewed change | Ticket context becomes a tested draft PR, never automatic merge | GitHub/agent tooling establishes the expected issue-to-PR workflow; SACM has Jira orchestration and draft PR service | `jira_orchestration_service.py`, `draft_pull_request_service.py`, Evidence Pack | Requires real sandbox E2E; dashboard diff/PR evidence is being completed | P0 | Unreviewed or untraceable code change | Staging Jira ticket produces a draft PR tied to immutable run/evidence identifiers. |
| Operational incident handling | Fleet capacity, failed jobs and recovery decisions are visible and controlled | Kubiya documents control-plane queues/workers; adjacent Kubernetes operations tools emphasize events and recovery | Executor health, operational health, dead-letter routes and Operations dashboard | SLO evaluation and recovery drill evidence missing | P1 | Silent stalled delivery | Authorized operator sees dead letters, records rationale, requeues once and observes a durable outcome. |
| Evidence-first audit | Reviewer follows requirement → decision → run → change → verification → PR | No direct equivalent is assumed; SACM differentiates on immutable evidence and cognitive provenance | Evidence service, traceability service, cognitive delivery passport endpoint | Passport view and end-to-end missing-link reporting need UI/API coverage | P0 | False claim of verified delivery | Passport displays signed/unsigned state, evidence links, provenance edges and explicit missing relations. |

## Deliberate product boundary

SACM should not replicate an IDE or generic agent console. Its differentiator is
the cross-agent, tenant-scoped decision and evidence layer. The next work is
therefore a trustworthy Run Detail and delivery passport, followed by staging
E2E and recovery evidence—not an autocomplete editor or unverified compliance
claims.
