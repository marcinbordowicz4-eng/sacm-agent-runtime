# SACM current-state audit

_Audited: 2026-10-03. This is a repository audit, not a production-readiness
attestation._

## P0 follow-up — console diagnostics and CI

| Concern | Status | Evidence | Remaining condition |
| --- | --- | --- | --- |
| Browser-to-API diagnostics | IMPLEMENTED AND VERIFIED (local) | `apps/api/main.py` emits `X-Request-ID`, `X-SACM-Version`, `X-SACM-Revision` and `GET /version`; `apps/dashboard/src/App.tsx` renders distinct network, configuration, authentication and authorization states | A deployed dashboard/API pair still needs a browser E2E check. |
| Cross-origin API access | IMPLEMENTED, PENDING DEPLOYMENT VERIFICATION | API allows explicit `SACM_CORS_ORIGINS`; production Compose defaults it to `https://sacm.io`, and `scripts/deploy-static-site.sh` builds the static console with `https://api.sacm.io` | Apply the configuration and run a browser E2E check against the deployed CloudFront/API pair. |
| Resumable first-run setup | IMPLEMENTED AND BUILT | Settings uses real organization/project/policy/enrollment endpoints and stores only non-secret drafts locally; dashboard production build passed | A staged executor enrollment and first mission are not possible without tenant, executor and repository credentials. |
| Main CI | FIXED, PENDING REMOTE VERIFICATION | GitHub Actions run `37105936562` failed only on Ruff import ordering in `apps/api/main.py`; `RUFF_CACHE_DIR=/private/tmp/sacm-ruff-cache ruff check sacm apps cli tests scripts` now passes | Push and observe a new CI run. |
| Security release gate | FIXED, PENDING IMAGE VERIFICATION | Run `37105936848` passed its 10 security tests but blocked on CVE-2026-103111 in `libpcre2-8-0` `10.46-1~deb13u2`; Dockerfile now requests the fixed package candidate | Local Docker daemon was unavailable, so the rebuilt image and Trivy result must be verified in CI. |

## System map

| Concern | Implemented evidence | Status | Gap / validation needed |
| --- | --- | --- | --- |
| API and contracts | FastAPI app in `apps/api/main.py`; Pydantic schemas in `sacm/schemas/` | IMPLEMENTED PARTIALLY VERIFIED | API build-metadata/request-ID contract and focused security tests pass locally; the full suite requires the supported dependency set. |
| Identity and tenancy | `sacm/core/auth_service.py`, `sacm/core/tenancy_service.py`, negative tenancy tests | IMPLEMENTED UNVERIFIED | Verify against the configured OIDC provider and PostgreSQL deployment. |
| Durable runs | `sacm/core/run_service.py`, hash-chained events, snapshots and recovery routes | IMPLEMENTED UNVERIFIED | PostgreSQL/Redis failure and load scenarios remain unexecuted here. |
| Policy and approvals | `sacm/core/policy_service.py`, `/v1/approvals`, OPA configuration, resource-digest migration | IMPLEMENTED UNVERIFIED | Local regression tests cover changed-resource and expiry behavior; execute the suite against the supported dependency set and PostgreSQL. |
| Executor plane | signed jobs, lease/heartbeat and recovery services under `sacm/core/` and `apps/api/routes/execution_plane.py` | IMPLEMENTED UNVERIFIED | Customer-managed executor and egress isolation need staging evidence. |
| Ticket-to-PR | Jira service/orchestration and draft PR service; `tests/test_jira_e2e.py` | IMPLEMENTED UNVERIFIED | Real Jira/GitHub sandbox execution has not been evidenced locally. |
| Evidence and supply chain | `sacm/core/evidence_service.py`, `sacm/core/supply_chain_service.py` | IMPLEMENTED UNVERIFIED | Production signing keys and object-store configuration are external prerequisites. |
| Cognitive State | immutable events, provenance relations and delivery passport in `sacm/core/cognitive_state_service.py` | IMPLEMENTED UNVERIFIED | Existing Jira E2E test asserts idempotent FEATURE snapshot and passport projection, but the full suite is not runnable in this checkout. |
| Dashboard | React/Vite app under `apps/dashboard`; Mission, Evidence, Operations, Policies views; bounded redacted timeline | IMPLEMENTED AND BUILT | `npm run build` passes locally; local `npm run lint` is blocked by a broken inherited dependency symlink, while CI's clean `npm ci` remains the authoritative lint check. API-connected browser E2E is missing. |
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
