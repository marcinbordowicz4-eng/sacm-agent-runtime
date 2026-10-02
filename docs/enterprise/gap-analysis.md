# Enterprise gap analysis

## P0 — security and correctness

| Gap | Impact | Repository next step | Exit evidence |
| --- | --- | --- | --- |
| No staging main-flow proof | Cannot claim ticket-to-PR correctness or absence of duplicate SCM effects | Configure an isolated Jira, GitHub repository, PostgreSQL, Redis and executor | One approved run produces one draft PR and Evidence Pack; worker restart does not duplicate the PR. |
| OIDC configuration not exercised | Identity lifecycle and claims mapping are unproven | Run contract tests with a test issuer and least-privilege memberships | Positive and cross-tenant denial evidence. |
| Sandbox/egress proof absent | Untrusted repository content could exceed intended execution boundary | Test the configured executor image and network policy | Path traversal, SSRF/egress and resource-limit tests. |
| Evidence signing key lifecycle unverified | Manifest signature cannot be trusted operationally | Integrate protected non-production signing key and rotation procedure | Valid, invalid and rotated-key verification test. |

## P1 — delivery process

| Gap | Impact | Repository next step | Exit evidence |
| --- | --- | --- | --- |
| Run-list URL filters and server paging | Large portfolios are not yet deeply navigable | Add compatible query contract and durable filter state | Browser test preserves filter/deep link and bounded response. |
| Change Review requirements mapping | Reviewer sees diff and matrix but no per-hunk requirement relation | Extend traceability projection only if exact source mapping is persisted | A changed file/hunk links to durable requirement and verification evidence. |
| Verification browser E2E | Dashboard integration has type/build proof, not browser proof | Add authenticated browser scenario for PASS, BLOCKED and unavailable states | Recorded browser run at desktop and narrow width. |

## P2 — operations and performance

| Gap | Impact | Repository next step | Exit evidence |
| --- | --- | --- | --- |
| SLO/load baseline | Capacity and responsiveness are unknown | Define workload and execute a reproducible load profile | Measured p95, error rate and queue recovery with constraints documented. |
| Alert delivery and incident drill | Failure may not reach an owner | Wire authorized non-production alert sink and run dead-letter drill | Alert, acknowledgement and recovery audit evidence. |

## P3 — expansion

Benchmarking against direct and simple agent baselines needs an authorized,
versioned task corpus and comparable model/executor budgets. The requested
100-task benchmark is therefore **BLOCKED**, not simulated, until those inputs
and a non-production execution environment are supplied.
