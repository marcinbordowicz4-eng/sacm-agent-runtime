# Security Policy

Report vulnerabilities privately through GitHub Security Advisories for this
repository. Do not open a public issue for a suspected vulnerability.

Include the affected version, reproduction steps, impact, and any proposed
mitigation. Reports are acknowledged within five business days. Fixes are
released after validation and coordinated disclosure.

Supported releases are the latest `main` build and the latest tagged release.

## Tenant and credential controls

Production APIs authenticate OIDC users or scoped opaque service credentials.
Raw service and executor tokens are returned only at issuance and must be
placed in a secret manager; SACM persists hashes only. Revoke credentials on
exposure, role change, workload retirement, or unexpected last-used activity.

Tenant authorization is enforced by service-layer resource checks using
organization/project attribution and task/run bridges. Cross-tenant failures
return generic 403/404 responses and must not expose resource metadata.
Authorization decisions and sensitive actions are append-only audit events
with a canonical SHA-256 chain per organization. Never place tokens, secrets,
private keys, repository content, or command output in audit request metadata.

After upgrading legacy databases, run the tenant backfill endpoint as an
organization owner/admin and resolve every reported item before enabling
production traffic. Ambiguous or unattributable legacy rows remain nullable
and fail closed in production rather than being assigned arbitrarily.

## Supply-chain signing

Evidence and release/image attestations use canonical JSON with SHA-256 and
prefer Ed25519. Private signing keys must be supplied through a mounted file or
secret broker and are never accepted by an API or persisted. Database records
may contain the public key, key ID, fingerprint, signature, and predecessor
hash. Legacy HMAC verification is retained for migration only.

Treat `INVALID`, subject/hash mismatch, malformed SBOM metadata, broken
attestation or Evidence Pack chains, and missing mandatory scan coverage as
release blockers. Secret scan artifacts are sanitized before Evidence Pack
copying; never upload raw credentials, private keys, or unredacted scanner
fixtures as evidence.
