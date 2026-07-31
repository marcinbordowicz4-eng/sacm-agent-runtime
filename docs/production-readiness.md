# Production Readiness

Data governance, privacy request, immutable audit export, and SIEM incident
procedures are documented in [enterprise-governance.md](enterprise-governance.md).

SACM is a secure and auditable software-delivery control plane for coding
agents. It governs identity, policy, approvals, durable runs, evidence, and
cost attribution; it does not replace agent frameworks or coding agents.

## Release gate

A release requires green lint, type checks, tests, package build, migration
validation, container build, CodeQL, and deployed HTTPS/OIDC smoke tests.
Completed delivery runs require policy-approved actions and evidence packs.
They also require successful, covered SBOM, provenance, dependency, secret,
IaC, and container records. Missing types are returned by the supply-chain
completeness API and on the run contract.

## Enterprise supply-chain gate

The workflow-level blocking contract and signed release report are documented
in [release-security-gate.md](release-security-gate.md). A missing scanner,
container build, SBOM, CodeQL result, signature, or adversarial test is
`INCOMPLETE` and blocks promotion rather than being replaced with synthetic
evidence.

Apply Alembic revision `f7b0c5d6e8a2` after `e6a9b4c5d7f1`. The migration is
dynamic-baseline compatible, adds Evidence Pack signature/chain metadata and
run completeness fields, and creates durable image, release, record, and
attestation tables. It stores public keys and signatures only, never private
keys.

Generate an Ed25519 evidence key with
`scripts/bootstrap-production-secrets.sh`. Mount it read-only and configure
`SACM_EVIDENCE_SIGNING_PRIVATE_KEY_FILE`; set a stable
`SACM_EVIDENCE_SIGNING_KEY_ID` and rotate by deploying a new key ID. HMAC file
or environment configuration remains compatibility-only. Verify every pack
and attestation chain before promoting an image or release. Any `INVALID`,
broken predecessor, subject mismatch, missing mandatory record, failed scan,
or empty coverage blocks production completion.

The production image carries OCI source/version/revision/build-date labels and
runs as the unprivileged `sacm` user. Validate the effective image user and
labels with `docker image inspect`, and verify the Compose configuration with
`docker compose -f docker-compose.production.yml config` before deployment.
The supply-chain workflow uploads evidence artifacts but has read-only
repository permissions and no publishing credentials or release step.

## Enterprise IAM release gate

Apply Alembic revision `d5f8a2b3c4e6` after
`c4e7f1a2b3d5`. The migration adds only nullable tenant columns to legacy
resource tables, creates scoped service credentials and tenant audit events,
and backfills unambiguous task/run/project bridges. It is safe for the dynamic
baseline path and does not force a non-null tenant onto old data.

After migration, call
`POST /v1/organizations/{organization_id}/tenant-backfill` with an identity
that has `data.manage`. Archive the returned report, investigate `unresolved`
records, and do not enable production access until all active resources are
attributed. The backfill is idempotent and records `tenant-attribution/v1`
source metadata.

Use organization/project-scoped service credentials for automation instead of
shared user tokens. Grant only required explicit permissions, set an expiry,
monitor `last_used_at`, and revoke through the organization API. The raw token
is shown once and is never stored by SACM.

Export organization audit events only with `audit.export`. Verify that
sequences are contiguous and each `previous_event_hash` matches the preceding
event hash before relying on an export for investigation or compliance.

## Current deployment boundary

The Lightsail deployment is a single-node production pilot, not a
high-availability service. It has no database failover, autoscaling, rolling
deployment, or tested host-loss recovery. Do not claim HA or benchmark
superiority until the documented benchmark protocol and recovery/load tests
are published.

## Isolated executor boundary

Production startup rejects `SACM_WORKFLOW_BACKEND=local`. The API schedules
signed jobs for separately deployed executors and does not treat
`X-SACM-Actor` as executor identity. Executor enrollment is one-use and scoped
to an organization, project, or customer deployment. Store the returned opaque
executor credential in that executor's secret manager; the control plane stores
only its hash. Revoke an executor immediately after key or credential exposure.

Job payloads are Ed25519-signed with
`SACM_JOB_SIGNING_PRIVATE_KEY_FILE`. The bootstrap script generates that key
without placing it in the database. Executors must verify payload canonical
JSON, SHA-256, signature, and signing-key fingerprint before execution. The
control plane performs the same checks against the executor's enrolled public
key for every result.

## Enterprise credential leases

Execution plans declare provider, resource, permissions, audience, and step
bindings without embedding values. Supported contracts are environment
(development compatibility only), Vault HTTP, AWS Secrets Manager or STS
(`.[secrets-aws]`), and Azure Key Vault or managed identity
(`.[secrets-azure]`). Provider configuration APIs accept metadata and
credential environment-variable names only; embedded credentials are rejected.

An active executor may issue a short-lived lease only for a declared
requirement of its owned job. Durable records contain value-free scope, policy,
handle, timestamps, use count, and at most a provider lease ID hash. The
one-time exchange fetches material in memory and returns only RSA-OAEP-256 plus
AES-256-GCM ciphertext wrapped to the executor's enrolled RSA public key.
Completion, failure, cancellation, executor revocation, expiry, and explicit
tenant-admin revocation invalidate leases.

Production requires a non-environment `SACM_SECRET_PROVIDER` included in
`SACM_APPROVED_SECRET_PROVIDERS`. The tenant/project provider configuration
must also be enabled and approved for production. Keep
`SACM_CREDENTIAL_LEASE_MAX_SECONDS` at 300 or lower where provider contracts
permit.

## gVisor/runsc prerequisite and fail-closed check

The Compose file does **not** install or claim gVisor on the host. Before
starting an executor, install `runsc` using the gVisor instructions for the
host, register it with the selected container runtime, then fail closed:

```bash
set -eu
command -v runsc >/dev/null
runsc --version
docker run --rm --runtime=runsc --security-opt=no-new-privileges \
  alpine:3.20 true
```

Do not start or enroll the executor if any command fails. Enrollment in
production requires `sandbox_runtime=runsc` (or a runtime explicitly added to
`SACM_APPROVED_SANDBOX_RUNTIMES`) plus a `sandbox-policy/v1` contract with
`host_runtime_verified=true`, the exact verification command, restricted or
deny-by-default networking, and `no_new_privileges=true`. Adding a runtime to
the approved list is a policy decision that must document why its isolation is
at least as strong as the current runsc baseline; `runc` is rejected.

Executors should run outside the control-plane host/network where practical.
Grant only required capabilities and labels, deny inbound access, restrict
egress to the API and approved source/artifact services, and never copy the
control-plane signing private key to an executor.

## Lease operations

Set a lease interval appropriate for executor heartbeat latency
(`SACM_EXECUTION_LEASE_SECONDS`, default 120 seconds). Executors must start and
heartbeat with the lease token returned by the lease endpoint. Expired leases
are atomically returned to the queue until attempts are exhausted; stale
tokens and wrong executor identities are rejected. Completion is idempotent
only when the canonical result hash is identical.

The production Compose file includes remote and Temporal connection settings,
but no bundled Temporal cluster. `remote` does not require Temporal. Point
`SACM_TEMPORAL_ADDRESS` at a separately operated cluster only when using the
Temporal backend.
