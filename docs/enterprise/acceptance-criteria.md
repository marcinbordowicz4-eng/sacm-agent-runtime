# Enterprise acceptance criteria

## P0 — governed delivery

| Criterion | Evidence required | Current status |
| --- | --- | --- |
| Tenant boundary | Cross-tenant read/decision returns denial and audit entry | Repository controls exist; staging proof required. |
| Plan-bound approval | An approval has a SHA-256 resource binding. Changing `plan_source_hash` produces a new pending approval and cannot authorize delivery. | Implemented with regression test; suite execution pending supported dependencies. |
| Approval expiry | An expired decision cannot authorize; retry creates a new pending request. | Implemented with regression test; PostgreSQL confirmation required. |
| Reviewable delivery | Completed sandbox ticket yields context, plan, policy result, approval history, execution events, diff/test evidence, Evidence Pack and draft PR. | Partial repository support; staging E2E required. |
| Evidence truthfulness | UI distinguishes verified, unverified, absent and unavailable data. | Implemented in dashboard patterns; browser E2E required. |
| No automatic merge | SCM handoff is a draft PR and requires a human merge outside SACM. | Repository behavior must be proven in sandbox. |

## P1 — operator usability

| Criterion | Evidence required |
| --- | --- |
| Run discovery | Server-side filters, pagination and stable URL state for organization/project/run. Event timeline paging is implemented; run-list filters and durable URL state remain. |
| Incident operation | Fleet/job state, dead-letter action, rationale and durable audit record. |
| Accessibility | Keyboard-only approval/rejection and mobile-width Run Detail browser tests. |
| Observability | Trace-linked latency, token, cost and quality fields with explicit unavailable states. Run event payloads are server-redacted before the dashboard receives them. |

## P3 — production operations

Production readiness requires an automated recovery drill, restore drill,
measured load of at least ten concurrent runs, alert routing test, secret
rotation test, vulnerability/SBOM gate and documented rollback. A successful
repository build is not evidence for these criteria.
