# Customer-hosted executor

The customer executor is a pull-based execution-plane daemon. The control plane
stores only tenant-scoped job metadata, signed `agent-task/v1` contracts,
context references, repository coordinates, hashes, and approved artifact
references. Source trees, workspaces, command output, credentials, and payload
source content remain inside the customer boundary.

## Trust and data flow

1. An administrator creates a single-use enrollment token.
2. `sacm-customer-executor enroll` creates an Ed25519 key in a `0700`
   identity directory, enrolls the public key and boundary declaration, and
   stores the returned bearer token as `0600`.
3. The daemon reports version, capabilities, capacity, residency, boundary,
   TLS/mTLS pin metadata, proxy, allowlist, and metadata-service blocking.
4. The control plane validates the declaration against the organization or
   project `governance_metadata.executor_network_policy` and residency policy.
5. A leased contract is hash checked and verified against the pinned
   control-plane Ed25519 key before an operator-approved sandbox runner receives
   it. Repository coordinates resolve only through the local `repository_map`;
   control-plane filesystem paths are never used.
6. Results are schema checked and signed. Local artifact paths are replaced by
   hashes; only boundary-approved upload URLs with hashes may leave the network.

The daemon never logs enrollment/auth/lease tokens, job contracts, source
content, subprocess output, or private keys. Local `/health`, `/status`, and
`/capacity` endpoints expose operational metadata only. Fleet health is
available from `GET /v1/executors/health` and emits executor-capacity/SLO data
through the existing OTLP hooks.

## Operations

Validate and enroll:

```bash
sacm-customer-executor validate-config --config /etc/sacm-executor/config.yaml
sacm-customer-executor enroll --config /etc/sacm-executor/config.yaml
```

Run under `deploy/customer-executor/sacm-customer-executor.service` or use the
gVisor deployment in `deploy/kubernetes/customer-executor.yaml`. Use `status`,
`drain`, `resume`, `rotate`, and `revoke-preparation` for lifecycle operations.
Rotation enters drain mode and replaces both key and bearer token. Revocation
preparation creates a non-secret handoff record for an administrator to call
the existing revoke API.

The control plane signs an `executor-update-manifest/v1` on every heartbeat.
The daemon verifies its hash, signature, pinned key, and minimum version. An
incompatible executor drains; it never self-updates. Operators transfer and
verify releases with:

```bash
python scripts/customer-executor-offline-bundle.py generate \
  --output executor-bundle.zip --private-key offline-release-key.pem artifacts/*
python scripts/customer-executor-offline-bundle.py verify executor-bundle.zip \
  --trusted-key-fingerprint <fingerprint-recorded-out-of-band>
```

## Air-gapped runbook

1. Set `deployment_type: air-gapped`, use an internal control-plane relay, an
   empty outbound allowlist, private PKI/mTLS, and pinned certificate/signing
   fingerprints. The daemon requires this customer-network-local relay; do not
   weaken HTTP/TLS checks or connect it directly to a public control plane.
2. Build the wheel/container and approved runner on a connected build system.
   Generate the signed offline bundle, record its fingerprint separately, scan
   it, and transfer it on controlled media.
3. Verify the bundle before installation. Pin the image by digest and disable
   image pulls. Provision identity and workspace volumes only; source
   repositories stay on customer-managed storage.
4. Enroll through the internal relay, then remove the one-time token. Validate
   `/health`, fleet capacity, lease heartbeat, audit delivery, and a
   hash-addressed test artifact.
5. For upgrades, set drain, wait for `active_job_id` to clear, verify a new
   bundle, install it manually, validate compatibility, and clear drain.
6. For suspected compromise, run `revoke-preparation`, revoke server-side,
   preserve audit evidence, replace the identity volume, and re-enroll with a
   new single-use token.

No durable schema change is required for this phase: capacity, network policy,
and update controls use versioned JSON contracts in existing executor records.
