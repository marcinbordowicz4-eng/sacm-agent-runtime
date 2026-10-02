# Enterprise implementation plan

## Milestone 1 — trustworthy delivery review

**User scenario:** an authorized engineering reviewer opens a completed Jira
delivery and can follow its requirement, plan, approvals, execution events,
captured diff, verification evidence, draft PR and cognitive FEATURE snapshot.

**Current change:** render the existing authorized Cognitive Delivery Passport
in the dashboard Evidence & Passports view. The UI obtains it only from
`GET /v1/projects/{project_id}/cognitive/deliveries/{delivery_id}/passport`
when durable Jira delivery context supplies a delivery identifier. It renders
absence rather than constructing provenance from client assumptions.

**Acceptance criteria:**

1. A completed Jira delivery with a cognitive record shows requirement count,
   linked Evidence Pack status, provenance edge count and FEATURE snapshot
   identifier/hash.
2. A non-Jira or incomplete run does not call a guessed endpoint and shows no
   fabricated cognitive state.
3. UI build and lint pass; the existing `test_completed_jira_delivery_creates_idempotent_cognitive_feature_snapshot` remains the service-level regression proof.

## Milestone 1 extension — reviewer-grade verification

**Current change:** the dashboard now loads the existing authenticated
`GET /v1/runs/{run_id}/verification` contract and renders requirement-level
status, build, regression, compatibility, security, test-integrity, evidence
completeness and explicit blockers. A 404 remains an explicit unavailable
state; the client never synthesizes a pass from test/artifact counters.

**Validation:** `npm run build` and `npm run lint` passed locally. Browser E2E
against an API remains an explicit next step.

## P0 extension — approval invalidation at execution time

External-agent result approvals now carry `execution_plan_id`,
`plan_revision` and `plan_source_hash`. Before a previously approved result can
complete a waiting step, SACM compares that binding with the latest durable
plan. A mismatch marks the approval `SUPERSEDED`; a re-submission creates a new
approval and records a `StepApprovalRebound` event without deleting history.

## Next milestones

| Order | Outcome | Dependencies | Exit evidence |
| --- | --- | --- | --- |
| P0.2 | Staging E2E Jira → executor → Evidence Pack → draft PR | Isolated GitHub sandbox, PostgreSQL, Redis, executor credentials | Automated test with no production repository mutation. |
| P0.3 | Approval fingerprint and plan-change invalidation | Policy/approval schema review | Positive and negative server-side tests. |
| P1.1 | Run Detail deep links, URL filters, server pagination and log retrieval/redaction | Bounded, server-redacted event-log API and timeline paging are implemented; deep links and URL filters remain | Browser E2E at desktop/mobile widths. |
| P1.2 | Snapshot comparison and stale-context state | Cognitive-state comparison API | Explicit COMPLETE/PARTIAL/TRUNCATED/STALE cases. |
| P1.3 | Organization configuration surfaces for agent/environment/secret-provider state | Existing tenancy and lease APIs | Permission-denied and no-secret-in-UI tests. |
| P3 | Recovery, restore and load evidence | Staging infrastructure and observability | Measured RPO/RTO, 10 concurrent runs, and recorded deviations. |

## Constraints

No production deployment, external account configuration, credential issuance
or GitHub mutation is authorized by this plan. Those steps remain blocked until
a staging environment and explicit authorization are supplied.
