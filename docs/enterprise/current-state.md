# SACM current state — Milestone 1

_Audited 2026-10-02. Statuses describe repository evidence and local validation,
not a production attestation._

| Capability | Status | Evidence |
| --- | --- | --- |
| API, relational models and Alembic migrations | IMPLEMENTED | `apps/api`, `sacm/infrastructure/db/models.py`, `sacm/migrations/versions/`. PostgreSQL rolling-migration verification is UNVERIFIED. |
| Runs, steps, state transitions and hash-chained events | IMPLEMENTED | `sacm/core/run_service.py`; `tests/test_run_service.py`. |
| Queue leases, worker recovery and dead-letter handling | IMPLEMENTED | `workflow_queue_service.py`, `execution_plane_service.py`, recovery routes. External-worker failure drill is UNVERIFIED. |
| Snapshot, restore, replay and comparison | IMPLEMENTED | `snapshot_service.py`, `tests/test_snapshot_replay_service.py`. Real storage/restore drill is UNVERIFIED. |
| Tenant authorization and RBAC | IMPLEMENTED | `tenancy_service.py`, production security tests and resource authorization in API routes. OIDC integration is UNVERIFIED without an IdP. |
| Plan-bound human approval | IMPLEMENTED | SHA-256 resource binding/expiry in `policy_service.py`; `tests/test_policy_service.py`. |
| Evidence Pack, provenance and integrity checks | IMPLEMENTED | `evidence_service.py`, `traceability_service.py`, verifier tests. Signing-key deployment is UNVERIFIED. |
| Secret redaction in reviewer data | IMPLEMENTED | Redacted event log, diff and artifact metadata in `apps/api/routes/runs.py`; `tests/test_run_detail_event_log.py`. |
| Run Detail reviewer workflow | IMPLEMENTED | Dashboard Run Detail renders plan, approvals, redacted event timeline, diff, Evidence Pack, delivery passport and verification matrix. API-connected browser E2E is UNVERIFIED. |
| Jira/GitHub delivery integration | PARTIAL | Orchestration and draft-PR services exist. An authorized sandbox Jira→executor→GitHub E2E run is MISSING. |
| Sandbox/egress enforcement | PARTIAL | Execution-plane policies and credential leases exist. Independent runtime isolation and egress test evidence is MISSING. |
| Production operations | PARTIAL | Health, executor/fleet views, runbooks and recovery primitives exist. Alert routing, load, backup/restore and SLO measurements are MISSING. |

## Local baseline

On 2026-10-02, the focused policy and Run Detail test selection completed in
**1.97 s**: `9 passed` (`tests/test_policy_service.py` and
`tests/test_run_detail_event_log.py`). The dashboard production build completed
in **254 ms** and `npm run lint` completed successfully. These are developer
workstation validation timings, not a capacity or latency claim.

See [current-state-audit.md](current-state-audit.md) for a broader map and
[implementation-plan.md](implementation-plan.md) for exit criteria.
