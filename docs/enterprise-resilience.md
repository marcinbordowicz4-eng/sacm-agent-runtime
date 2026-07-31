# Enterprise resilience runbook

## Scope and topology

`deploy/kubernetes/enterprise-resilience.yaml` is the HA-ready reference artifact:
three API replicas, three workers, three Temporal frontends, disruption budgets,
required pod anti-affinity, readiness checks, gVisor `RuntimeClass`, a separate
migration Job, and secret references for external PostgreSQL and Redis services.
Replace image placeholders and provide `sacm-runtime-secrets`,
`temporal-database`, and `sacm-backup-keys` before applying it.

`docker-compose.production.yml` remains a single-host operational profile.
Replica declarations or process restarts do **not** make Compose highly
available; use the Kubernetes topology (or an equivalent orchestrator across
failure domains) for HA.

## Backup catalog and logical backup

Backups and DR drills are durable in `backup_records` and
`disaster_recovery_drills`. Catalog entries contain tenant/global scope,
credential-free storage URI, SHA-256 checksum, encryption key metadata,
artifact/evidence metadata, status, size, timestamps, and RPO/RTO targets.

Configure PostgreSQL credentials only through `SACM_BACKUP_DB_PASSWORD`,
`SACM_BACKUP_DB_PASSWORD_FILE`, or `SACM_BACKUP_DB_PGPASSFILE`. They are passed
in the child environment, never command arguments or API responses. Configure
age recipients/identity files and keep them in a secret store.

```bash
python scripts/postgres-logical-backup.py \
  --database sacm \
  --storage-uri file:///backups/sacm-2026-07-31.dump.age \
  --rpo-seconds 3600 --rto-seconds 1800
```

Only paths below `SACM_BACKUP_ROOT` are accepted. Storage URIs containing
credentials, query strings, fragments, or unsupported schemes are rejected.

## Restore verification and DR drills

The default restore flow verifies the checksum, decrypts to a restricted
scratch path under the backup root, creates a random isolated database, runs
`pg_restore`, then checks readiness, schema presence, and unvalidated foreign
keys. It records measured RPO/RTO and drops the isolated database.

```bash
python scripts/verify-postgres-restore.py --backup-id BACKUP_ID
```

Production is never overwritten by default. A destructive restore additionally
requires `--destructive`, an explicit target database, and the value held in
`SACM_DESTRUCTIVE_RESTORE_GUARD(_FILE)`. Rotate the guard after emergency use.
Prefer an isolated drill and require incident-command approval before invoking
the destructive path.

## Execution recovery

Run `scripts/recover-execution-jobs.py` periodically (the Kubernetes manifest
uses a two-minute CronJob). It recovers expired leases, missing lease owners,
revoked executors, and missing lease tokens. RUNNING steps are reconciled to
PENDING for retry or FAILED when attempts are exhausted. Exhausted jobs enter
`DEAD_LETTER`; operators can inspect and requeue them through:

- `POST /v1/operations/execution/recover`
- `GET /v1/operations/execution/jobs?state=DEAD_LETTER`
- `POST /v1/operations/execution/jobs/{id}/requeue`

Requeue is tenant-authorized and does not reset attempts unless explicitly
requested.

## SLOs and operational health

The durable SLO contract API covers availability, job start latency, completion
rate, evidence coverage, audit delivery/chain continuity, backup freshness,
RPO, and RTO. `POST /v1/slo/defaults` creates defaults; customize with
`PUT /v1/slo/contracts`, then persist evaluations with
`POST /v1/slo/evaluate`. Error budgets are computed as:

`allowed_bad = total * (1 - objective)` and
`remaining = allowed_bad - observed_bad`.

`GET /v1/operations/health` returns no credentials or endpoint URLs. It
aggregates database status, optional/required Redis and OPA probes, executor
capacity, queue depth/age, backup freshness, tenant audit-chain continuity, and
signing-key configuration. Each call stores an operational snapshot for the
availability SLO.

## Telemetry and alerts

Enable OTLP with `SACM_OTEL_ENABLED=true`. Metrics cover queue depth/age, lease
recovery, executor capacity, backup outcomes/freshness, restore duration, and
SLO outcomes. `config/otel-collector.yaml` exposes a Prometheus exporter on
port 8889. Load `config/alerts/enterprise-resilience.yaml` into the monitoring
system and confirm its normalized metric names against the collector version.

Alert response:

1. Queue stalled/no capacity: inspect active executors and recover leases.
2. Backup stale/failure: run a fresh encrypted backup and isolated drill.
3. DR failure: quarantine the artifact on checksum failure; do not restore it.
4. SLO breach: inspect the durable evaluation details and error budget before
   resuming risky changes.
