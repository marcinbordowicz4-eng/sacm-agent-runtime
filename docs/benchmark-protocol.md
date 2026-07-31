# Benchmark 100 Protocol

> **READINESS: IMPLEMENTED, NOT EXECUTED.** The checked-in suite and fixture
> manifests are reproducible. This repository contains no fabricated model run.
> A real benchmark remains `NOT_RUN` until an external agent/model and, for
> SACM, an enrolled execution-plane executor are configured.

## Suite and fixtures

`benchmarks/suite-v2.json` is `BenchmarkSuiteV2`: exactly 100 original
engineering tasks, balanced at 20 per language (Python, TypeScript, React,
Java, and Go). Categories are fixed at 20 bugs, 20 features, 15 refactors,
15 migrations, 10 security tasks, 10 multi-repository changes, and 10
reliability/recovery tasks. Every case records pinned local-template Git
revisions, acceptance criteria, verification commands, command/tool allowlists,
timeout/token/cost budgets, risk, and expected artifacts.

`benchmarks/fixture-manifest-v2.json` pins the deterministic commits. Generate
small working repositories outside the source tree:

```bash
sacm benchmark validate
sacm benchmark generate benchmark-work
```

Generation uses original Apache-2.0 fixture code, a fixed author and timestamp,
and no network access. Generated repositories and build output are intentionally
not checked in. The task starts with a failing acceptance check.

## Real runners only

The baseline runner invokes a configured external single-agent CLI and passes
the case contract on stdin. The command must write
`.benchmark-agent-result.json` using `benchmark-agent-result/v2`, with matching
provider/model/version, `simulated: false`, provider usage/cost, attempts,
interventions, coverage, and findings. It never substitutes a built-in answer. The SACM
runner creates the run and `AgentTaskV1`, signs and schedules an
`ExecutionJob` through `ExecutionPlaneService`, and waits for an enrolled
external executor. Complete provider/model/model-version, agent/runtime
versions, and runner configuration are mandatory.

```bash
sacm benchmark run --runner baseline-command \
  --config private-baseline-config.json --fixtures benchmark-work \
  --output baseline-v2.json

sacm benchmark run --runner sacm-execution-plane \
  --config private-sacm-config.json --fixtures benchmark-work-sacm \
  --ablation sacm-full --output sacm-v2.json
```

Missing configuration produces explicit `NOT_RUN`; a missing enrolled executor
or executable produces `BLOCKED`. Neither is counted as an attempt. Simulated
outputs are invalid.

## Evidence and validity

Report v2 records provider/model/version, agent/runtime version, Git/OS/tool
versions, dirty state, wall time, provider-reported usage/cost, attempts,
interventions, verification, requirement coverage, regression/security
findings, accepted outcome inputs, and hash-addressed evidence. Validation
rejects:

- missing or hash-mismatched evidence;
- simulated results or attempted cases without external execution;
- mixed configurations, altered/unequal budgets, or unpinned fixtures;
- duplicate, omitted, or incomplete cases.

The offline **accepted PR proxy** is explicitly defined as: all verification
commands pass, requirement coverage is 100%, no regression or security finding
is present, and a Git diff artifact exists. It is not a hosted pull request,
maintainer review, merge, or production outcome.

## Comparison and ablations

Comparison reports solved count, accepted PR proxy, regression rate,
requirement coverage, interventions, cost per accepted result, recovery time,
duration, token usage, and security violations. Paired deterministic bootstrap
95% intervals are emitted only when at least 10 cases were actually attempted
by both configurations. Otherwise status is `INSUFFICIENT_SAMPLE`, no
comparative claim is emitted, and exclusions are listed.

Supported arms are SACM full, no reviewer, no policy, no replay/recovery, and
the single-agent baseline. Apart from the named ablation, use identical suite,
fixture revisions, model where technically possible, budgets, hardware class,
network policy, and stopping rules.

```bash
sacm benchmark compare baseline-v2.json sacm-v2.json
sacm benchmark report benchmark-comparison-v2.json \
  --output benchmark-comparison-v2.md
```

## Legal and fair-comparison caveats

Fixtures contain no copied third-party application source. Do not publish
credentials, proprietary prompts, private repository content, or provider
terms-prohibited traces. Model/provider differences, nondeterminism, rate
limits, tool availability, executor locality, warm caches, and human
intervention can confound results; disclose them and exclude unequal pairs.
This benchmark measures these tasks under the recorded configuration only and
does not establish general superiority, security, legal compliance, or
production fitness.
