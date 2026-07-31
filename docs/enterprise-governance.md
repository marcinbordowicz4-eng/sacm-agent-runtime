# Enterprise governance

Enterprise governance is configured under
`/v1/organizations/{organization_id}`. Policy and privacy operations require
`data.manage`; signed audit exports require `audit.export`. Project-scoped
service credentials cannot access another project or organization.

## Retention and residency matrix

Every active policy must define all categories. Values below are recommended
starting points, not hard-coded defaults.

| Category | Typical classification | Retention | Deletion | Export | Typical storage |
|---|---|---:|---|---|---|
| source context | Confidential | 30–90 days | cryptographic | allowed | encrypted regional |
| task metadata | Internal | 365 days | tombstone | allowed | regional database |
| runtime events | Confidential | 90–365 days | cryptographic | allowed | regional database |
| logs | Confidential | 30–90 days | hard delete | restricted | regional log archive |
| artifacts | Confidential | 90–365 days | cryptographic | allowed | encrypted object storage |
| evidence | Restricted | 7 years | preserve/tombstone | allowed | immutable archive |
| backups | Restricted | 30–90 days | cryptographic | restricted | encrypted cold storage |
| analytics | Internal | 395 days | tombstone | aggregate only | regional database |
| audit | Restricted | 7 years | preserve | allowed | append-only archive |

Each rule stores classification (`Public`, `Internal`, `Confidential`, or
`Restricted`), retention days, legal-hold state, deletion mode, exportability,
allowed regions, allowed storage classes, and evidence preservation behavior.
Project policy overrides organization policy. Activating a version retires the
previous active version for that scope.

Region and classification columns are nullable for rolling upgrades. Run the
governance metadata backfill endpoint after setting organization/project
defaults. New evidence, artifacts, tenant backups, and executor storage use the
active rule. Production rejects an explicitly disallowed region or storage
class.

## Export and deletion procedure

1. Create a tenant or data-subject request.
2. Obtain independent approval; the requester cannot approve their request.
3. Run inventory. This is mandatory and creates a checksummed dry-run item list.
4. Review legal holds, non-exportable categories, and evidence preservation.
5. Process in bounded batches. The durable cursor makes processing resumable.
6. Archive the final manifest and deletion receipts.

Deletion never silently removes inventory records. Each item records a
tombstone, cryptographic key-destruction attestation, hard-delete receipt,
preservation outcome, or legal-hold block. Evidence defaults to preservation
unless an active rule explicitly permits another action.

## Audit export runbook

Set an Ed25519 private key using
`SACM_AUDIT_EXPORT_SIGNING_PRIVATE_KEY_FILE` and a stable key identifier using
`SACM_AUDIT_EXPORT_SIGNING_KEY_ID`. Create an audit batch for the organization,
download its canonical manifest, and verify:

- manifest SHA-256 checksum;
- every event checksum and previous-hash link;
- chain root and end hashes;
- Ed25519 signature and public-key fingerprint.

Audit batches are immutable. A failed verification is an incident: stop
distribution, retain the suspect file, record its checksum, rotate the signing
key only if compromise is suspected, compare the source audit chain, and create
a new batch rather than editing the old one.

## SIEM delivery runbook

HTTP endpoints must be explicitly host-allowlisted. Production requires HTTPS;
credentials in URLs, redirects, loopback/private/link-local destinations, query
credentials, and fragments are rejected. Database records contain only hashes
of credential/signing references. Resolve those hashes through
`SACM_SIEM_SECRET_<HASH_PREFIX>` or the corresponding `_FILE` variable.

Deliveries use canonical JSON, HMAC-SHA256 signatures, checksums, and stable
idempotency keys. Failed deliveries retry with exponential backoff and then
enter dead letter. Drain through the organization endpoint or schedule:

```bash
python scripts/drain-siem.py --organization-id ORG_ID
```

For an incident:

1. Pause the sink and preserve delivery/error metadata.
2. Check governance health and dead-letter count; never copy resolved secrets
   into tickets or logs.
3. Validate endpoint allowlist, DNS resolution, TLS, and the external receiver's
   idempotency handling.
4. Rotate the external secret reference if exposure is suspected.
5. Re-enable the sink and explicitly retry dead-letter deliveries.
6. Confirm the cursor advances and audit-delivery health returns to healthy.

Operational health includes governance request backlog, pending SIEM delivery,
active sink, and dead-letter counts. Alert on any dead letter and on sustained
approved/inventoried request backlog.
