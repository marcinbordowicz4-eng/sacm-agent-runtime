# SACM Agent Runtime

SACM Agent Runtime is a production-oriented MVP for the **Shared Agent Context Model**: a multi-agent orchestration runtime that coordinates specialist agents, persistent memory, routing, repository operations, and API/CLI access for software delivery workflows.

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

## Quick start

```bash
cp .env.example .env
docker-compose up --build
```

API will be available at `http://localhost:8000`.

## CLI usage

```bash
sacm init
sacm register-repo /path/to/repository
sacm run "Fix failing tests in checkout flow" --repo /path/to/repository
sacm events <task-id>
sacm memory <task-id>
sacm diff <task-id>
```

## MCP server

SACM can run as a local stdio MCP server. It gives a calling coding agent a
persistent task briefing and explicit repository operations:

- `sacm_advise` creates a SACM task and returns its compiled context.
- `sacm_run_agents`, `sacm_get_task`, `sacm_get_events`, `sacm_get_memory`, and
  `sacm_add_memory` manage the agent workflow and persistent context.
- `sacm_apply_patch`, `sacm_run_verification`, and `sacm_get_diff` perform
  repository work through the SACM API.

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
