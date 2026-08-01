# SACM Agent Runtime

[![CI](https://github.com/marcinbordowicz4-eng/sacm-agent-runtime/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/marcinbordowicz4-eng/sacm-agent-runtime/actions/workflows/ci.yml)
[![CodeQL](https://github.com/marcinbordowicz4-eng/sacm-agent-runtime/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/marcinbordowicz4-eng/sacm-agent-runtime/actions/workflows/codeql.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Licensed under the [Apache License 2.0](LICENSE).

SACM Agent Runtime is a production-oriented MVP for the **Shared Agent Context Model**: a multi-agent orchestration runtime that coordinates specialist agents, persistent memory, routing, repository operations, and API/CLI access for software delivery workflows.

> **Positioning:** SACM is a secure and auditable software-delivery control
> plane for coding agents. It turns GitHub issues into policy-governed,
> reproducible draft pull requests with evidence, rather than replacing
> LangGraph, OpenAI Agents SDK, OpenHands, or coding agents themselves.

## What SACM is

SACM provides a shared execution model for multiple agents working on the same task. It combines:

- a task lifecycle stored in PostgreSQL
- event and memory capture for cross-agent context sharing
- a router backed by PyTorch for agent selection
- repository tooling for file analysis, patch application, diffs, and command execution
- FastAPI and Typer interfaces for automation

## Architecture overview

```text
CLI / API clients
      |
      v
FastAPI routes  <---->  Core services
      |                 - Orchestrator
      |                 - Task / Event / Memory / State services
      |                 - Context compiler / Router / Verifier
      v
Agent registry  <---->  Agent implementations
      |                 - Reasoner / Coder / Reviewer / Tester
      |
      v
Infrastructure adapters
- SQLAlchemy + PostgreSQL/pgvector
- Redis client
- Git repository / worktree helpers
- Docker / shell execution
- isolated customer or SACM executor services
```

### Versioned agent contracts

Every orchestration step is dispatched as `AgentTaskV1` and returns
`AgentResultV1`. The persisted task event includes both serialized contracts,
while SACM's legacy context and result models are retained only as a migration
adapter for existing agents. Contract results carry artifact references,
verification evidence, provider-reported usage, decisions, and failure details.

### Autonomous diagnosis and recovery

Failed agent steps are normalized into `failure-report/v1` classifications:
compilation, test regression, wrong assumption, missing context, architecture
mismatch, bad plan, API incompatibility, environment, tool failure, or a stuck
model. SACM deterministically chooses code repair, debugging, replanning,
context expansion, model switching, retry, or escalation. Recovery attempts
reuse the original task budget, are capped by `SACM_MAX_RECOVERY_ATTEMPTS`
(default `3`), and are recorded in the hash-chained run events, snapshots,
evidence manifest, and operational context. External agents receive the
diagnosis and recovery instructions in the retried `AgentTaskV1`.

Structured `diagnostic-bundle/v2` inputs preserve command, exit code, tool,
compiler diagnostics, failed tests, stack traces, changed symbols, affected
requirements, environment errors, prior attempts, graph context, and patch
identity. Deterministic adapters normalize Maven/Gradle/javac, pytest/mypy/Ruff,
TypeScript/Jest/Vitest, Playwright/Cypress, Go, Terraform, Helm, kubeconform,
JUnit XML, and JSON-line output before classification. Diagnoses include
concrete evidence, calibrated confidence, reason codes, root cause, and a stable
fingerprint. Confidence below `SACM_DIAGNOSTIC_MIN_CONFIDENCE` (default `0.6`)
escalates to a human, and an identical patch/root-cause pair is never retried.

Authenticated recovery APIs are:

```text
GET  /v1/runs/{run_id}/recovery
POST /v1/runs/{run_id}/recover
```

### Outcome-adaptive routing

`POST /v1/router/rank` combines the existing PyTorch router prior with durable
agent outcomes, cost, latency, retries, verification evidence, project history,
task tags, risk, and previous failure classifications. Outcome data cannot
override the neural fallback until `SACM_ROUTER_MIN_SAMPLES` real samples
(default `3`) exist for a candidate. Decisions return ranked candidates,
confidence weights, penalties, reasons, and the data scope used. A `success`
outcome is explicitly an execution proxy and is not represented as human
acceptance.

### Governed task intake

Issue trackers submit a connector-neutral `TaskContractV1` to
`POST /v1/intake/tasks`. SACM persists the source identity, prevents duplicate
tasks, calculates a deterministic Definition of Ready score, and creates
durable clarification questions for missing descriptions, acceptance criteria,
repository references, or ownership. Jira webhooks can use
`POST /v1/intake/jira/webhooks`; plain text and Atlassian Document Format
descriptions are normalized into the same contract.
In production, contracts must identify a SACM project through `project_id` or
a repository reference mapped to a project. Task context, plans, requirements,
traceability, and clarifications then enforce project membership and minimum
viewer/developer roles.

### Jira Cloud ticket-to-delivery

The production Jira Cloud connector stores only secret references and project
mapping. It verifies configured webhook signatures, durably deduplicates
deliveries, normalizes ADF and mapped custom fields, and keeps one idempotent
SACM status comment plus configured Jira workflow transitions. Clarification
answers are accepted only through an exact marker:

```text
[SACM-CLARIFICATION:v1 clarification_id=<durable UUID>]
<answer; JSON is required for list/object fields>
```

Configure with `POST /v1/jira/connectors`, receive webhooks at
`POST /v1/jira/webhooks/{connector_id}`, then call
`POST /v1/jira/connectors/{connector_id}/tasks/{task_id}/orchestrate`.
SACM builds Definition-of-Ready, application impact, policy-governed planning,
a project-scoped run, and remote execution jobs. Without an active executor it
reports `WAITING_FOR_EXECUTOR`; without explicit GitHub delivery configuration
it reports `PR_NOT_CONFIGURED`. It never claims a completed run or PR.

Run the deterministic offline scenario:

```bash
sacm jira-e2e-demo
```

The command clearly labels Jira Cloud, the executor, and GitHub as simulated,
uses fake network transports, scans the fixture repositories under
`examples/jira-e2e-demo`, and uses the real SACM intake, context, planning, and
run services. Expected readiness rises from `0.65` to `1.0`; the impact spans
the storefront contract, payments service, and orders database. Evidence and
PR delivery remain explicitly pending until a real executor/configuration is
available.

### Enterprise IAM and tenant isolation

Production authentication accepts either configured OIDC bearer tokens or
opaque `sacm_service_...` credentials. Service credentials are issued once;
only a SHA-256 token hash and non-secret prefix are stored. Credentials have an
organization scope, optional project scope, role, additive permissions,
expiry, revocation, and last-used timestamp.

The default owner/admin/developer/viewer roles remain available, with explicit
permissions for `runs.read/write/execute`, `tasks.read/write`,
`evidence.read/build`, `approvals.read/decide`, `executors.manage/use`,
`audit.export`, and `data.manage`. Authorization is enforced in core services,
not only API routes. Tenant attribution is stored on tasks, runs, context,
memory, artifacts, evidence, approvals, snapshots/replays, plans, jobs, and
executors; legacy rows remain nullable until safely attributed through
task/run/project bridges.

Organization IAM APIs:

- `POST/GET /v1/organizations/{organization_id}/service-credentials`
- `POST /v1/organizations/{organization_id}/service-credentials/{id}/revoke`
- `GET /v1/organizations/{organization_id}/audit-events`
- `POST /v1/organizations/{organization_id}/tenant-backfill`

Authorization and sensitive actions append canonical, per-organization
SHA-256 hash-chained audit events. Audit metadata excludes bearer tokens and
other secret-like values.

### Application context and impact analysis

Build task-scoped application context with
`POST /v1/tasks/{task_id}/application-context`, then retrieve the durable
snapshot or its focused impact/risk view with:

- `GET /v1/tasks/{task_id}/application-context`
- `GET /v1/tasks/{task_id}/application-context/impact-risk`

All three endpoints use the same authentication as other `/v1` APIs. A build
resolves every `TaskContractV1.repositories` entry through SACM's repository
path controls. Local `path` values are validated by `RepositoryAdapter`;
`full_name` values must map to a project repository path or an entry in
`SACM_GITHUB_REPOSITORIES_JSON`. Missing, unmapped, nonexistent, and
out-of-root repositories are persisted as explicit `unavailable` entries, so
a partial multi-repository context cannot appear complete.

The deterministic scanner records repository, module, file, dependency,
HTTP-route, database/schema, symbol, and test-symbol nodes. Its directed edges
cover containment, imports, declarations, calls, tests, route implementation,
and schema representation. Python symbols use AST ownership; JavaScript,
TypeScript, Java, Kotlin, C#, and Go use bounded declaration extraction. The
scanner excludes hidden/generated/vendor directories, reads no file larger than
1 MB, scans at most 5,000 files per repository, and caps the complete graph at
20,000 nodes and 40,000 edges. Impact ranking uses normalized terms from the
task title, description, labels, and acceptance criteria, with one-hop graph
propagation. The risk score is a deterministic 0-100 sum of returned factors
such as unavailable repositories, API/schema/dependency reach, impact breadth,
cross-repository scope, sensitive task terms, and scan truncation.

Context Engine V2 refreshes that graph during agent execution and creates a
bounded, SHA-256-addressed `context-package/v2`. Seeds can come from changed or
failing symbols, changed files, failed tests, affected requirements, or the
task impact set. Role-aware traversal follows callers, callees, related tests,
imports, routes, and schemas, then safely reads only repository-owned excerpts.
Every package is persisted as a tenant-attributed event, can be tied only to a
run owned by the same task, and is compacted together with excerpts under the
agent token budget. `EXPAND_CONTEXT` recovery increases traversal depth and
node limits without discarding prior failure evidence.

Context Engine V2.1 can merge canonical semantic facts from an optional SCIP
index without treating syntax heuristics as type-aware resolution. Generate a
repository-local JSON index with:

```text
scip print --json index.scip > .sacm/index.scip.json
```

`SACM_SCIP_JSON_PATH` may select another safe repository-relative path. SACM
validates canonical document paths, isolates document-local symbols, preserves
definition/reference/test and implementation/type relationships, records the
indexer name/version and SHA-256 fingerprint, and caps index size, documents,
symbols, occurrences, and relationships. When no valid index exists, the
existing deterministic syntax graph remains an explicit fallback. This adapter
contract also permits future LSP or Sourcegraph-backed implementations without
changing context-package consumers.

Authenticated package APIs are:

```text
POST /v1/tasks/{task_id}/context-package
GET  /v1/tasks/{task_id}/context-package?run_id={run_id}
```

### Governed execution planning

After a task is Definition-of-Ready and has durable application context, build
its execution plan with:

```text
POST /v1/tasks/{task_id}/execution-plan
GET  /v1/tasks/{task_id}/execution-plan
GET  /v1/tasks/{task_id}/execution-plan/policy
GET  /v1/tasks/{task_id}/execution-plan/security-review
GET  /v1/tasks/{task_id}/execution-plan/secret-requirements
```

The optional build body is `{"policy_pack": "default"}` or
`{"policy_pack": "strict"}`. Planning deterministically decomposes acceptance
criteria and description statements, attaches ranked application-context
nodes, assigns registered agents by portable roles, and persists versioned
`execution-plan/v1` and `execution-plan-step/v1` contracts. Agent
configurations use `agent-task/v1` and `agent-result/v1` adapter boundaries;
external frameworks can implement those contracts without adding their SDKs to
the runtime.

Every plan includes durable risk and OPA-compatible policy decisions, a pending
security-review gate with versioned findings, and approval requirements for
application risk, privileged tools, schema changes, deployments, and
security-sensitive work according to the selected policy pack. Planning gates
can reference the existing durable `Approval` record when execution creates a
run.

Secret requirements must be explicit `secret-request/v1` objects in
`TaskContractV1.metadata.secret_requests`, for example:

```json
{
  "schema_version": "secret-request/v1",
  "name": "deployment-token",
  "purpose": "Authenticate the deployment tool",
  "environment_variable": "DEPLOYMENT_TOKEN",
  "required": true
}
```

The environment broker performs exact-name availability checks and returns
only opaque handles and non-secret metadata. Secret values are never written to
database rows, events, logs, or API responses.

### Run snapshots, restore, and replay

SACM creates durable `run-snapshot/v1` checkpoints at safe run transitions and
step boundaries. Each snapshot anchors the run and task identity, workflow
version, run and step state, execution-plan/context summary, event sequence and
hash, parent snapshot, creation reason, and a deterministic SHA-256 checksum.
Manual creation with the same state and reason is idempotent.

Authenticated snapshot and replay APIs are:

```text
GET  /v1/runs/{run_id}/snapshots
POST /v1/runs/{run_id}/snapshots
GET  /v1/runs/{run_id}/snapshots/{snapshot_id}
POST /v1/runs/{run_id}/restore
POST /v1/runs/{run_id}/replay
GET  /v1/runs/{replay_run_id}/comparison
```

Restore is deliberately different from `POST /v1/runs/{run_id}/resume`.
Ordinary resume only moves a failed run back to planning and retains its latest
step state. Restore requires a failed run and a resumable selected checkpoint,
validates its checksum and event-chain anchor, rejects changed step topology,
and reinstates the captured run/step state before appending `SnapshotRestored`.

Replay never changes the source run. It creates a new run and durable
`run-replay/v1` link to the source run and snapshot. Requests may record
`model`, `provider`, and `framework` overrides plus a replay reason. The
`replay-comparison/v1` response reports both runs' status, steps, usage, cost,
evidence, failures, and latest available output summary.

### Requirement traceability and Evidence Pack 2.0

SACM derives durable `requirement/v1` records from
`TaskContractV1.acceptance_criteria` and previously recorded
`bdd_requirement_registered` events. Requirement text is normalized and
SHA-256 hashed; IDs are deterministic for the task and normalized content, so
refreshing persisted data does not renumber unchanged requirements.

Authenticated traceability APIs are:

```text
GET  /v1/tasks/{task_id}/requirements
POST /v1/tasks/{task_id}/requirements/refresh
GET  /v1/tasks/{task_id}/traceability
POST /v1/tasks/{task_id}/traceability/refresh
POST /v1/tasks/{task_id}/traceability/links
```

Refresh rebuilds derived `requirement-link/v1` records from durable application
and execution plans, assigned agents, context/runtime events, run steps,
repository audit events, commits, diff hashes, changed files, tests,
verification, policy/security/approval decisions, artifacts, and evidence
packs. External CI, review, or deployment systems can submit an explicit link
with a requirement ID, target type and ID, relation, optional run ID, and
non-secret metadata. External links are retained across derived refreshes.

`traceability/v1` reports deterministic total, covered, evidence-covered, and
per-target metrics plus the complete explicit list of uncovered requirements.
Source-only BDD registration links do not count as implementation coverage.

Verifier V2 replaces model confidence as the completion authority for tasks
with mandatory acceptance criteria. Its durable `verification-matrix/v2`
requires every criterion to map to implementation, tests, executed commands,
and hash-valid evidence; a deterministic build; a focused regression test that
fails on the pre-fix revision and passes after the change; affected-area
regression; API/schema compatibility; security verification; test-integrity
proof; and complete evidence. Tool results must carry deterministic provenance
and a zero exit code. Removed tests, weakened assertions, missing criteria, or
self-reported model success block completion. The matrix is available through
`GET /v1/runs/{run_id}/verification` and is embedded in Evidence Pack 2.0.

Evidence generation retains the existing `run-manifest/v2` fields and adds the
backward-compatible Evidence Pack 2.0 sections: task contract/source and
readiness, application graph hash/impact/risk, execution plan and portable
agent assignment, policy/security/approvals, commands/tests/files/commits,
usage/cost, snapshot/replay provenance, requirement links and coverage,
artifact checksums, event-chain provenance, and integrity/signature metadata.
Secret-like fields and configured secret values are redacted before any pack
JSON or text artifact is written. Retrieve a checksum-verified manifest with:

```text
POST /v1/runs/{run_id}/evidence
GET  /v1/runs/{run_id}/evidence
GET  /v1/runs/{run_id}/evidence/{evidence_id}/manifest
POST /v1/runs/{run_id}/evidence/{evidence_id}/verify
POST /v1/runs/{run_id}/evidence/verify-chain
```

### Enterprise software supply chain

Versioned `supply-chain-record/v1`, image, release, provenance, attestation,
and verification contracts persist SBOMs and scan results against exact
SHA-256 subjects. SPDX JSON and CycloneDX JSON receive lightweight structural,
artifact-hash, and subject-identity validation without loading parser
frameworks. Supported mandatory production evidence types are SBOM,
provenance, dependency scan, secret scan, IaC scan, and container scan; run
responses expose `supply_chain_status` and `missing_supply_chain_evidence`.

Provenance uses deterministic canonical JSON and records source/revision,
builder/executor, commands, materials/products, agent/model/framework,
policy/security decisions, snapshots/replay, environment, and image digest.
Attestations and Evidence Packs prefer Ed25519 keys loaded only from
`SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE`, retain legacy HMAC verification, and
record only public key material, fingerprints, key IDs, signatures, and chain
hashes. Verification returns explicit `VALID`, `INVALID`, or `UNSIGNED`.

Use `/v1/runs/{run_id}/supply-chain/...` for records, provenance, images,
releases, attestations, chain verification, and completeness. Local release or
image attestations can be generated and checked without an API:

```bash
python scripts/supply-chain-attestation.py generate \
  --subject dist/package.whl --predicate release.json \
  --predicate-type https://sacm.dev/release/v1 \
  --key secrets/evidence_signing_private_key --output attestation.json
python scripts/supply-chain-attestation.py verify --attestation attestation.json
```

`.github/workflows/supply-chain.yml` builds the image, generates an SPDX SBOM,
runs dependency/secret/IaC/container scans, emits canonical provenance, and
uploads evidence only. It never publishes a release.

### Outcome analytics dashboard

SACM materializes deterministic `outcome-analytics/v1`,
`step-outcome-analytics/v1`, and `agent-outcome-analytics/v1` rows from durable
run state. Metrics include success/failure/cancelled outcomes, latency,
retries, provider/model token usage and cost, requirement and evidence
coverage, policy blocks, approvals, security findings, snapshot/replay source
links, changed files, tests, verification, and recorded failures. Re-reading an
unchanged run recomputes the same source fingerprint and does not duplicate
rows or change their `computed_at` value.

Authenticated analytics APIs are:

```text
GET /v1/runs/{run_id}/analytics
GET /v1/analytics/tasks/{task_id}
GET /v1/analytics/projects/{project_id}
GET /v1/analytics/organizations/{organization_id}
```

Run analytics enforce the run's project membership. Project and organization
aggregates require viewer access; task aggregates authorize every represented
project. Production mode rejects legacy run/task analytics without tenancy.
Historical data is not guessed: unavailable timings, usage, cost, coverage,
policy, or security signals are returned as explicit `null` values with
`data_state`, `legacy_data`, and per-source `data_completeness` indicators.
Only persisted `agent_result` events become agent analytics; `system` and
`user` runtime actors are never presented as agents.

The dependency-free React/Vite Mission Control in `apps/dashboard` consumes
only authorized SACM APIs. Its navigation includes Command Center, Missions,
Applications, Agents, Policies, Evidence & Change Passports, Benchmarks,
Security, and Settings. Command Center reports real outcome/cost/coverage,
policy and security blocks, executor capacity, and operational SLO/backup/audit
health while preserving explicit null and legacy states. Mission View combines
readiness and clarification UX, risk-based autonomy, application impact,
plans/jobs/agents, Change Journey events, verification, snapshots/replay,
traceability and evidence. The application map is accessible and dependency
free; agent rankings distinguish measured outcomes, insufficient samples and
benchmarks that were not run. API/auth configuration appears only in Settings,
and the global command palette performs navigation and safe UI actions only.
Start it with:

```bash
cd apps/dashboard
npm run dev
```

Set `VITE_SACM_API_URL` when the API is not available through `/api`.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

API will be available at `http://localhost:8000`.
See [`docs/local-delivery-stack.md`](docs/local-delivery-stack.md) for optional
Temporal, OpenTelemetry, MLflow, OPA, benchmark, security, and E2E setup.

## CLI usage

```bash
sacm init
sacm register-repo /path/to/repository
sacm run "Fix failing tests in checkout flow" --repo /path/to/repository
sacm events <task-id>
sacm memory <task-id>
sacm diff <task-id>
sacm runs create "Fix failing tests" --repo /path/to/repository
sacm runs inspect <run-id>
sacm runs execute <run-id>
sacm runs cancel <run-id>
sacm runs resume <run-id>
sacm runs retry <run-id> <step-id>
sacm runs evidence <run-id>
sacm benchmark validate
sacm benchmark generate benchmark-work
sacm benchmark run --runner baseline-command --config runner.json
sacm benchmark compare baseline-v2.json candidate-v2.json
sacm benchmark report benchmark-comparison-v2.json
```

## MCP server

SACM can run as a local stdio MCP server. It gives a calling coding agent a
persistent task briefing and explicit repository operations:

- `sacm_advise` creates a SACM task and returns its compiled context.
- `sacm_run_agents`, `sacm_get_task`, `sacm_get_events`, `sacm_get_memory`, and
  `sacm_add_memory` manage the agent workflow and persistent context.
- `sacm_apply_patch`, `sacm_run_verification`, and `sacm_get_diff` perform
  repository work through the SACM API. Pass the `task_id` returned by
  `sacm_advise` so implementation hashes, changed files, verification results,
  and concise memory updates are persisted with the task.

Start the API first, then configure an MCP client to run:

```bash
sacm-mcp
```

Set `SACM_API_URL` to the API address accessible from the MCP process.

## Integrating with another repository

Store SACM metadata in `.sacm.yaml` at the target repository root:

```yaml
repository:
  path: .
  default_branch: main
commands:
  build: pytest -q
  test: pytest -q
constraints:
  - Never modify production secrets
  - Prefer minimal diffs
```

Pass the repository path through the API or CLI when creating tasks.

## API endpoints

- `GET /health`
- `POST /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/run`
- `GET /tasks/{task_id}/events`
- `GET /tasks/{task_id}/memory`
- `GET /tasks/{task_id}/artifacts`
- `POST /agents/register`
- `GET /agents`
- `GET /agents/{agent_id}`
- `PATCH /agents/{agent_id}`
- `POST /memory/search`
- `POST /memory/add`
- `POST /router/route`
- `GET /v1/runs/{run_id}/snapshots`
- `POST /v1/runs/{run_id}/snapshots`
- `POST /v1/runs/{run_id}/restore`
- `POST /v1/runs/{run_id}/replay`
- `GET /v1/runs/{replay_run_id}/comparison`
- `GET /v1/runs/{run_id}/recovery`
- `POST /v1/runs/{run_id}/recover`
- `GET /v1/runs/{run_id}/verification`
- `GET /v1/tasks/{task_id}/requirements`
- `GET /v1/tasks/{task_id}/traceability`
- `POST /v1/tasks/{task_id}/traceability/links`
- `POST /v1/runs/{run_id}/evidence`
- `GET /v1/runs/{run_id}/evidence/{evidence_id}/manifest`
- `POST /context/compile`
- `POST /context/ingest`
- `POST /repository/analyze`
- `POST /repository/create-worktree`
- `POST /repository/apply-patch`
- `POST /repository/run-tests`
- `POST /repository/diff`
- `POST /github/issues`
- `POST /github/branches/push`
- `POST /github/pull-requests`
- `GET /github/pull-requests/{number}/comments`
- `POST /github/pull-requests/{number}/merge`
- `POST /v1/runs`
- `GET /v1/runs/{run_id}`
- `GET /v1/runs/{run_id}/context`
- `POST /v1/runs/{run_id}/agent-steps`
- `POST /v1/runs/{run_id}/agent-steps/{step_id}/result`
- `POST /v1/runs/{run_id}/execute`
- `POST /v1/runs/{run_id}/cancel`
- `POST /v1/runs/{run_id}/resume`
- `POST /v1/runs/{run_id}/steps/{step_id}/retry`
- `GET /v1/runs/{run_id}/events`
- `POST /v1/runs/{run_id}/evidence`
- `POST /v1/tasks/{task_id}/application-context`
- `GET /v1/tasks/{task_id}/application-context`
- `GET /v1/tasks/{task_id}/application-context/impact-risk`
- `POST /v1/tasks/{task_id}/execution-plan`
- `GET /v1/tasks/{task_id}/execution-plan`
- `GET /v1/tasks/{task_id}/execution-plan/policy`
- `GET /v1/tasks/{task_id}/execution-plan/security-review`
- `GET /v1/tasks/{task_id}/execution-plan/secret-requirements`

## Delivery integrations

`CodexExecutor` runs `codex exec --full-auto --json` in a new task-scoped Git
worktree. It returns the branch, diff, Codex log, and configured build/test
results without modifying the source worktree. The Codex CLI must be installed
and authenticated where SACM runs.

`GitHubDelivery` preflights the GitHub CLI. The GitHub bridge creates issues and
pull requests, pushes branches, and reads review comments. Merging requires
`"confirmation": "merge-when-green"`; branch protection remains responsible for
required checks and human approval.

Copy `templates/react-native/` into a matching target repository to add EAS
preview/production, CodeQL, dependency review, and Maestro wallet/transaction
flows. Replace `APP_ID` and element IDs with target-app values before enabling
`SACM_RUN_MOBILE_E2E=true`. The agent reports executed-flow evidence, never a
checklist as a test result.

Set `SACM_CODEX_AUTO_CREATE_PR=true` to have `CodexExecutor` commit, push, and
open a **draft** PR after Codex succeeds **and** at least one configured verification
command passes. It never merges the PR. The task cost endpoint
`GET /tasks/{task_id}/costs` aggregates only provider-reported token usage
persisted for that task; tool durations are trace metadata, not inferred cost.

## OpenAI Agents SDK

Install `pip install -e ".[agents]"`, set `SACM_OPENAI_AGENTS_ENABLED=true`,
`OPENAI_API_KEY`, and optionally `SACM_OPENAI_AGENTS_MODEL` to enable
`OpenAIAgentsExecutor`. It uses `trace_include_sensitive_data=False`; SACM
persists only returned usage counters and an optional configured cost estimate.
Set the matching `SACM_OPENAI_AGENTS_{INPUT,OUTPUT}_COST_PER_MILLION_USD`
variables before relying on cost totals.

## External agent frameworks

LangGraph, Microsoft Agent Framework, OpenHands, Codex, and other orchestrators
can join a SACM run without an in-process adapter. Create an external agent step
with `POST /v1/runs/{run_id}/agent-steps`, execute the returned `AgentTaskV1`,
then submit its `AgentResultV1` to the step's `/result` endpoint. SACM validates
run and step identity, persists reported usage and evidence, and creates a
durable approval when the result status is `NEEDS_APPROVAL`.

## Enterprise execution plane

Set `SACM_WORKFLOW_BACKEND=remote` to turn run execution into a durable,
capability-aware `ExecutionJob` rather than invoking `LocalWorkflow`. Jobs link
to their run, run step, task, project/organization or customer deployment and
move through `QUEUED`, `LEASED`, `RUNNING`, and terminal states. PostgreSQL
workers acquire rows with locking and `SKIP LOCKED`; SQLite uses a conditional
claim for deterministic tests. Lease tokens are returned once and only their
domain-separated SHA-256 hashes are stored.

Organization/project admins use:

- `POST /v1/executors/enrollment-tokens`
- `GET /v1/executors`
- `GET /v1/executors/health`
- `POST /v1/executors/{executor_id}/revoke`

An executor exchanges its one-use enrollment token at
`POST /v1/executors/enroll`, then uses its opaque bearer credential with:

- `POST /v1/executor/heartbeat`
- `POST /v1/executor/rotate`
- `POST /v1/executor/jobs/lease`
- `POST /v1/executor/jobs/{job_id}/start`
- `POST /v1/executor/jobs/{job_id}/heartbeat`
- `POST /v1/executor/jobs/{job_id}/complete`
- `POST /v1/executor/jobs/{job_id}/fail`

Payload and result contracts use canonical JSON and SHA-256. Job payloads are
signed by an Ed25519 control-plane key loaded from the environment or a secret
file; executor results must verify against the public key and fingerprint
stored at enrollment. SACM never stores private signing keys or raw lease,
enrollment, or executor authentication tokens. Executor endpoints never use
`X-SACM-Actor` as service identity.

The production customer daemon, network-boundary policy, manual signed update
flow, systemd/Kubernetes deployments, and air-gapped operations are documented
in [`docs/customer-hosted-executor.md`](docs/customer-hosted-executor.md).

## Delivery policy and GitHub webhook

`ToolGateway` evaluates external actions through OPA when `SACM_OPA_URL` is set;
an unavailable OPA endpoint denies the action unless explicitly configured
otherwise. Without OPA, `SACM_DENIED_TOOL_ACTIONS` and
`SACM_APPROVAL_REQUIRED_ACTIONS` define local policy. Pending approvals are
available at `GET /v1/approvals` and resolved with
`POST /v1/approvals/{approval_id}/decision`.

`POST /github/webhooks` accepts only HMAC-verified GitHub webhooks. A labeled
issue creates a durable run only when the label matches
`SACM_GITHUB_TRIGGER_LABEL` and its `repository.full_name` is explicitly mapped
to a local path in `SACM_GITHUB_REPOSITORIES_JSON`. The webhook never executes
the run inline.

## Workflow backends

Local development keeps `SACM_WORKFLOW_BACKEND=local`. Production rejects that
backend and should use `remote`; `temporal` remains available for deployments
that install `.[temporal]` and run a separate worker. The legacy Temporal worker
still invokes the local workflow activity and therefore is not an isolated
executor substitute.

## Benchmarks

Benchmark 100 uses the checked-in versioned suite and deterministic local
fixture manifest under `benchmarks/`. It contains exactly 100 balanced,
version-pinned tasks across Python, TypeScript, React, Java, and Go. The
baseline invokes a real configured external coding-agent CLI; the SACM arm
schedules a real `AgentTaskV1` on the durable execution plane. Missing model,
executor, or credential configuration is reported as `NOT_RUN`/`BLOCKED`, never
as a result. Report v2 validates hash-addressed evidence, equal budgets, and
complete cases; comparison emits paired bootstrap intervals only with the
minimum completed sample. See [the protocol](docs/benchmark-protocol.md).

## Environment variables

See `.env.example` for defaults:

- `DATABASE_URL`
- `REDIS_URL`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `DEFAULT_EMBEDDING_PROVIDER`
- `DEFAULT_CONTEXT_DIM`
- `SACM_WORKTREE_ROOT`
- `SACM_MAX_AGENT_STEPS`
- `SACM_DEFAULT_TOKEN_BUDGET`
- `SACM_WORKFLOW_BACKEND`
- `SACM_JOB_SIGNING_PRIVATE_KEY_FILE`
- `SACM_APPROVED_SANDBOX_RUNTIMES`
- `SACM_SECRET_PROVIDER`
- `SACM_APPROVED_SECRET_PROVIDERS`
- `SACM_CREDENTIAL_LEASE_MAX_SECONDS`
- `SACM_EXECUTION_LEASE_SECONDS`
- `SACM_REMOTE_REQUIRED_CAPABILITIES`

## Cost telemetry

SACM uses OpenTelemetry for production cost analysis. Set
`SACM_OTEL_ENABLED=true` and configure `OTEL_EXPORTER_OTLP_ENDPOINT` to send
traces and metrics to an OTLP-compatible collector. OpenTelemetry records only
operational metadata, reported OpenAI embedding input tokens, and the optional
`sacm.gen_ai.estimated_cost` metric. Prompts, embeddings, and agent output are
never attached to telemetry.

Set `SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD` to the provider's
current input-token price before relying on estimated-cost metrics. The value is
deliberately not hard-coded, because provider pricing changes independently of
this runtime. Metrics are grouped by provider, model, operation, and token type;
task IDs are trace attributes only, avoiding high-cardinality metric labels.

MLflow is not used for runtime cost telemetry: it is better suited to offline
experiments that train or compare the PyTorch router. SACM includes two
opt-in agents for these distinct jobs:

- `OpenTelemetryCost` assesses whether the task has a collector and current
  embedding price configured before cost analysis.
- `MLflowExperiment` logs a privacy-preserving router-experiment baseline to
  MLflow. Install it with `pip install -e ".[mlflow]"`, then set
  `SACM_MLFLOW_ENABLED=true` and, where needed, `MLFLOW_TRACKING_URI`.

Neither agent logs prompts, embeddings, or task descriptions. MLflow should be
extended with outcome metrics once the router gains an offline
training/evaluation dataset.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Running tests

```bash
pytest tests/ -v
ruff check .
mypy .
```

## Docker

```bash
docker-compose up --build
```

The default container starts `uvicorn apps.api.main:app --host 0.0.0.0 --port 8000`.

## Production deployment

The included Compose stack is for local development only. A production process
must run database migrations before accepting traffic and set
`SACM_ENVIRONMENT=production`. Startup then refuses to run unless OIDC is
enabled, PostgreSQL is configured, OPA is fail-closed, evidence signing is
configured, the workflow backend is not local, an Ed25519 job-signing key is
configured, sandbox runtimes are fail-closed, a non-environment secret broker is
approved, and legacy/direct action APIs are
disabled. The production Compose default schedules remote jobs; it does not
install gVisor on the host. See `docs/production-readiness.md` before enrolling
an executor. Terminate TLS and enforce request limits at the deployment
ingress; expose only `/health` and `/ready` to infrastructure probes.

The image runs `sacm-migrate` before the API. For a non-container deployment,
run `sacm-migrate` once per release under the same `DATABASE_URL` before
starting application replicas.
For the 0.2.0 operational changes, migration sequence, release gates, and
rollback boundary, see [`docs/releases/v0.2.0.md`](docs/releases/v0.2.0.md).
Enterprise retention/residency policy, legal-hold-aware export and deletion,
signed immutable audit batches, and SIEM delivery are described in
[`docs/enterprise-governance.md`](docs/enterprise-governance.md).
