# SACM Agent Runtime

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
```

### Versioned agent contracts

Every orchestration step is dispatched as `AgentTaskV1` and returns
`AgentResultV1`. The persisted task event includes both serialized contracts,
while SACM's legacy context and result models are retained only as a migration
adapter for existing agents. Contract results carry artifact references,
verification evidence, provider-reported usage, decisions, and failure details.

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
sacm benchmark run benchmark-suite.json --output benchmark-report.json
sacm benchmark compare baseline.json benchmark-report.json
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
- `POST /v1/runs/{run_id}/agent-steps`
- `POST /v1/runs/{run_id}/agent-steps/{step_id}/result`
- `POST /v1/runs/{run_id}/execute`
- `POST /v1/runs/{run_id}/cancel`
- `POST /v1/runs/{run_id}/resume`
- `POST /v1/runs/{run_id}/steps/{step_id}/retry`
- `GET /v1/runs/{run_id}/events`
- `POST /v1/runs/{run_id}/evidence`

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

## Temporal backend

The local durable backend is the default. Install `pip install -e ".[temporal]"`,
set `SACM_WORKFLOW_BACKEND=temporal`, and run `sacm-temporal-worker` separately
to submit each API execution to Temporal. The worker invokes the same persisted,
idempotent local workflow activity.

## Benchmarks

`BenchmarkService` executes an explicit JSON suite and records only returned
statuses and measured duration. Compare two reports with
`sacm benchmark compare baseline.json candidate.json`; no benchmark score is
claimed unless both reports contain real executions.
Validate the required 50-case, non-placeholder suite before a formal run with
`sacm benchmark validate benchmark-suite.json`.

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
configured, and legacy/direct action APIs are disabled. Terminate TLS and
enforce request limits at the deployment ingress; expose only `/health` and
`/ready` to infrastructure probes.

The image runs `sacm-migrate` before the API. For a non-container deployment,
run `sacm-migrate` once per release under the same `DATABASE_URL` before
starting application replicas.
