# Release security gate

`config/release-security-policy.v1.json` is the versioned release contract.
`.github/workflows/supply-chain.yml` first collects evidence, then runs a
separate blocking gate. Collection steps continue only so JSON and SARIF
reports can be uploaded with `if: always()`; scan findings are evaluated again
by `scripts/security-release-gate.py` and cannot be hidden by an action result.

## Severity and evidence policy

- Dependency and container vulnerabilities block at `HIGH` or `CRITICAL`.
  `ignore_unfixed` is configured in the versioned policy and mirrored in the
  Trivy action. Change both in the same reviewed pull request.
- Secret findings block at every Trivy severity.
- IaC misconfigurations block at or above `minimum_block_severity` (`HIGH`).
- Missing or malformed reports, SBOM, signed provenance, tool metadata,
  CodeQL metadata, or JUnit evidence produce `INCOMPLETE`, never a pass.
- The SPDX SBOM hash must match the signed Ed25519 provenance subject. The
  provenance source revision and CodeQL metadata must match the gated git SHA.
- Every identifier in `required_adversarial_tests` must be present and passed
  in the JUnit report.

No scan result is synthesized when Docker or a scanner is unavailable. The
uploaded evidence remains useful for diagnosis, while the release result is
`INCOMPLETE`.

Before upload, secret JSON and SARIF are sanitized to remove matched values,
code, snippets, and message text while retaining rule identifiers, severity,
target, and finding counts. If sanitization fails, the raw secret reports are
deleted and the gate becomes `INCOMPLETE`.

## Exceptions

Exceptions are policy changes, not workflow inputs. Add a narrowly scoped
entry to `exceptions` through normal review:

```json
{
  "control": "scan.dependency",
  "owner": "security-team@example.com",
  "reason": "CVE-YYYY-NNNN is unreachable; removal tracked in ISSUE-123",
  "expires_at": "2026-08-15T00:00:00Z"
}
```

Supported controls are `scan.dependency`, `scan.container`, `scan.secret`, and
`scan.iac`. Expired, malformed, ownerless, reasonless, or unsupported
exceptions fail the gate. Exceptions never waive missing evidence, failed
adversarial tests, CodeQL, SBOM hashes, provenance, or signatures. Remove the
entry when remediated; do not extend an expiry without a new risk review.

## Verification and release evidence

Run the same verifier against downloaded workflow artifacts:

```bash
python scripts/security-release-gate.py evaluate \
  --policy config/release-security-policy.v1.json \
  --artifacts security-release-evidence \
  --junit security-release-evidence/security-tests.xml \
  --git-sha "$(git rev-parse HEAD)" \
  --output security-release-evidence/release-security-report.json

python scripts/security-release-gate.py verify-report \
  --signed-report security-release-evidence/release-security-report.signed.json
```

`evaluate` and `verify-report` exit `0` only for `PASS`, `1` for `FAIL` or an
invalid signature, and `2` for `INCOMPLETE`.

The report contains test identifiers and outcomes, scan summaries, policy
version, git SHA, observed tool versions, and SHA-256 hashes for every consumed
evidence file. CI signs it with an ephemeral Ed25519 key embedded in the signed
statement. Passing non-pull-request reports also receive a GitHub OIDC release
attestation. The private ephemeral key is deleted before artifact upload.

## Incident escalation

Do not release on `FAIL`, `INCOMPLETE`, signature failure, unexpected evidence
hash changes, secret findings, or suspected artifact tampering. Preserve the
workflow run and artifacts, stop promotion, revoke exposed credentials or
leases, and notify the security owner through the private process in
`SECURITY.md`. For suspected compromise, also isolate affected executors,
retain SIEM/audit evidence, identify impacted tenants, and begin coordinated
incident response before rerunning the gate.
