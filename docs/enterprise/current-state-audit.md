# SACM current-state audit

_Audited: 2026-10-02. This is a repository audit, not a production-readiness
attestation._

## System map

| Concern | Implemented evidence | Status | Gap / validation needed |
| --- | --- | --- | --- |
| API and contracts | FastAPI app in `apps/api/main.py`; Pydantic schemas in `sacm/schemas/` | IMPLEMENTED UNVERIFIED | CI declares API tests, but this workstation lacks Python dependencies. |
| Identity and tenancy | `sacm/core/auth_service.py`, `sacm/core/tenancy_service.py`, negative tenancy tests | IMPLEMENTED UNVERIFIED | Verify against the configured OIDC provider and PostgreSQL deployment. |
| Durable runs | `sacm/core/run_service.py`, hash-chained events, snapshots and recovery routes | IMPLEMENTED UNVERIFIED | PostgreSQL/Redis failure and load scenarios remain unexecuted here. |
| Policy and approvals | `sacm/core/policy_service.py`, `/v1/approvals`, OPA configuration, resource-digest migration | IMPLEMENTED UNVERIFIED | Local regression tests cover changed-resource and expiry behavior; execute the suite against the supported dependency set and PostgreSQL. |
| Executor plane | signed jobs, lease/heartbeat and recovery services under `sacm/core/` and `apps/api/routes/execution_plane.py` | IMPLEMENTED UNVERIFIED | Customer-managed executor and egress isolation need staging evidence. |
| Ticket-to-PR | Jira service/orchestration and draft PR service; `tests/test_jira_e2e.py` | IMPLEMENTED UNVERIFIED | Real Jira/GitHub sandbox execution has not been evidenced locally. |
| Evidence and supply chain | `sacm/core/evidence_service.py`, `sacm/core/supply_chain_service.py` | IMPLEMENTED UNVERIFIED | Production signing keys and object-store configuration are external prerequisites. |
| Cognitive State | immutable events, provenance relations and delivery passport in `sacm/core/cognitive_state_service.py` | IMPLEMENTED UNVERIFIED | Existing Jira E2E test asserts idempotent FEATURE snapshot and passport projection, but the full suite is not runnable in this checkout. |
| Dashboard | React/Vite app under `apps/dashboard`; Mission, Evidence, Operations, Policies views; bounded redacted timeline | IMPLEMENTED AND BUILT | `npm run build` and `npm run lint` pass locally; API-connected browser E2E is missing. |
| Production topology | Kubernetes reference, Compose pilot, alerts and runbooks under `deploy/`, `config/`, `docs/` | PARTIAL | No evidence of an independent HA deployment, restore drill, or measured SLO. |

## Main flow coverage

`Issue → context → plan → policy → approval → execution → verification →
evidence → draft PR` has implementation paths, but cannot truthfully be marked
end-to-end verified without an authorized staging environment containing
PostgreSQL, Redis, a sandboxed executor, Jira and a non-production GitHub
repository.

The dashboard is now being extended to render the API's authoritative cognitive
delivery passport. It must show explicit absence when the delivery projection
or snapshot is unavailable; it does not infer a snapshot from a run status.

## Release assessment

**NOT PRODUCTION-READY.** The repository contains many production-oriented
controls, but no current evidence in this workspace proves external integration
flows, recovery drills, load targets or deployment security review. See
`implementation-plan.md` for exit criteria.
