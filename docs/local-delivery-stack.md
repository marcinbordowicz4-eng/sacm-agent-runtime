# Local delivery stack

Copy `.env.example` to `.env`, then start the core runtime:

```bash
docker compose up --build
```

The API, PostgreSQL, Redis, and OPA are bound only to localhost. Start the
optional observability services with:

```bash
docker compose --profile observability up --build
```

For Temporal dispatch, set `SACM_WORKFLOW_BACKEND=temporal` in `.env`, then run:

```bash
docker compose --profile temporal up --build
```

## Repository mounts

The API container can access only the host directory configured through
`SACM_HOST_REPOSITORY_ROOT`; it is mounted at `/repositories`. Place each target
repository beneath that directory and create runs using its container path, for
example `/repositories/my-project`. Do not mount a home directory or any path
containing unrelated repositories or credentials.

## Local policy and approvals

The included OPA policy permits sandbox execution and requires an approval for
`github.create_draft_pr`. Update `config/opa/sacm.rego` for local experiments.
Do not use this development policy as a production authorization policy.

## End-to-end verification

1. Set `SACM_GITHUB_WEBHOOK_SECRET` and map the intended local repository in
   `SACM_GITHUB_REPOSITORIES_JSON`.
2. Configure the corresponding GitHub webhook to `POST /github/webhooks` and
   apply the `sacm` label to an issue. Confirm it creates a run with
   `GET /v1/runs`.
3. Execute the returned run and create its evidence pack:

   ```bash
   sacm runs execute <run-id>
   sacm runs evidence <run-id>
   ```

4. Configure build/test commands for the target repository, then enable
   `SACM_CODEX_AUTO_CREATE_PR=true` only after successful local verification.
   Confirm that any resulting PR is a draft and that no merge occurs.

## Benchmarks and security

Copy `benchmarks/local-suite.example.json`, replace every placeholder with a
real deterministic task, and execute it:

```bash
sacm benchmark run benchmark-suite.json --output benchmark-report.json
```

Run the suite again after a change and compare reports:

```bash
sacm benchmark compare baseline.json benchmark-report.json
```

The repository CI workflow runs lint and tests; CodeQL runs on pull requests,
the main branch, and a weekly schedule. Enable GitHub dependency review and
secret scanning in repository settings.
