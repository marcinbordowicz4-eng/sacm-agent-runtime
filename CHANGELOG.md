# Changelog

## Unreleased

## 0.2.0 - 2026-07-31

### Added

- Governed connector-neutral intake and a production Jira Cloud
  ticket-to-delivery path with deterministic offline E2E coverage,
  clarifications, deduplication, status synchronization, and truthful pending
  executor/PR states.
- Application intelligence for multi-repository context, dependency/impact
  analysis, deterministic risk scoring, governed planning, policy decisions,
  security review, secret requirements, approvals, and token/cost budgets.
- Durable run snapshots, restore/replay, requirement traceability, Change
  Journey history, and Evidence Pack 2.0 with hash-addressed verification,
  provenance, cost, policy, security, and coverage records.
- Typed autonomous failure diagnosis with bounded code repair, debugging,
  replanning, context expansion, model switching, retry, and explicit
  escalation. Recovery decisions persist in run state, events, snapshots,
  evidence, external agent contracts, and authenticated APIs.
- Outcome analytics and the Mission Control dashboard for missions,
  applications, agents, policies, evidence, benchmarks, security, executor
  capacity, and operational health.
- Enterprise remote and customer-hosted execution with signed jobs, sandbox
  controls, tenant-aware IAM and hash-chained audit, credential brokerage,
  supply-chain records/attestations, resilience and recovery controls,
  retention/residency governance, immutable audit export, and SIEM delivery.
- Benchmark 100 suite v2 and pinned deterministic fixture manifests. Its
  release status is **NOT_RUN**: no model/executor comparison was executed and
  this release makes no performance, quality, or superiority claim.
- A blocking release security gate covering CodeQL, dependency/container/IaC
  scans, secret evidence sanitization, adversarial tests, SBOM, signed
  provenance, and explicit `FAIL`/`INCOMPLETE` handling.

### Breaking and operational notes

- Python 3.11 or newer remains required. Production deployments must use OIDC
  or scoped service credentials, fail-closed policy/security configuration,
  signed evidence/jobs, an approved secret provider, and a non-local workflow
  backend.
- Run `sacm-migrate` once before starting 0.2.0 application replicas. The
  migration chain has one head, `d4f7a9c2e6b1`, and adds tenant, planning,
  application-context, snapshot/replay, traceability, analytics, execution
  plane, supply-chain, resilience, governance, IAM, secrets, and Jira data.
- Customer executors must report version `0.2.0`, be re-enrolled when signing
  or mTLS identity changes, and satisfy the configured minimum version,
  capability, region, sandbox, and network-boundary policy.
- Existing nullable legacy tenant fields require the documented organization
  backfill before strict tenant enforcement. Validate backups and a restore
  rehearsal before migration; rollback requires restoring the pre-upgrade
  database and artifacts rather than running destructive downgrades.
