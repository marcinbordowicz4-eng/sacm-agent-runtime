# SACM threat model

## Scope and trust boundaries

The in-scope flow is `Issue → context → plan → policy → approval → executor →
verification → evidence → draft PR`. Trust boundaries exist between the web
client/API, tenant authorization layer, policy decision point, queue/executor,
ticket/SCM integrations, evidence/object storage and operator identity.

| Threat | Control present in repository | Required verification / remaining work |
| --- | --- | --- |
| Cross-tenant read or action | Tenant-aware resource authorization, project/organization ownership and audit services | Negative API tests plus OIDC-backed staging test. |
| Approval replay after plan change | Canonical SHA-256 resource binding; different resource digest creates a fresh pending request | Run policy tests on supported Python/DB; include plan source hash in each gated plan resource. |
| Stale approval replay | Optional `SACM_APPROVAL_TTL_SECONDS`; expired pending/approved request transitions to `EXPIRED` | Define tenant policy defaults; test clock/expiry in PostgreSQL. |
| Privileged tool bypass | Local/OPA policy gateway with fail-closed OPA path and durable approval requirement | Enforce that all integration adapters use `ToolGateway`; add contract tests per adapter. |
| Secret disclosure | Credential/lease services and separation of dashboard configuration from secret value | Scan API responses, structured logs and error paths; run secret-scanning in CI. |
| Forged or altered evidence | Evidence manifests, hashes, signing/verification services and provenance records | Configure protected signing keys and object storage; perform tamper/revocation drill. |
| Duplicate external effect after worker failure | Queues, leases, idempotency and snapshot/recovery services | Kill/restart executor during draft-PR staging flow and prove one effect. |
| Malicious repository workspace | Scoped run repository paths and server-side run authorization for diff/artifact access | Isolate executor filesystem/network; add path traversal and egress tests. |
| Dashboard state spoofing | API-derived state and explicit optional-endpoint errors | Browser E2E with unauthorized, missing and stale responses. |

## Security decisions

Approval is authorization for the canonical resource, not for an action name in
the abstract. The resource must contain stable plan identity (for example,
`plan_source_hash`) whenever a plan is gated. TTL is deliberately disabled by
default for compatibility until an organization chooses a policy value; that
choice must be documented before production use.

## Non-goals

This document does not claim certification, penetration-test coverage or a
production threat-model review. Those require deployed topology, identities,
keys and external integrations that are not available in this workspace.
