# Benchmark Protocol

SACM does not claim quality, latency, cost, or PR-success results without
published execution evidence.

## Required evaluation

Use at least 50 deterministic, version-pinned repository tasks with explicit
acceptance criteria. Each task must record the repository revision, model
configuration, policy configuration, wall-clock duration, provider-reported
token usage, verification commands, and final outcome.

Compare SACM against a single-agent baseline using the same repositories,
models where applicable, command limits, and budget. At minimum include a
single coding-agent loop and any alternative system being claimed against.

Publish raw JSON reports from `sacm benchmark run`, a comparison report from
`sacm benchmark compare`, methodology, exclusions, and failures. Placeholder
suites and simulated runs are invalid evidence.
