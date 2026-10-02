# Production-readiness decision

**Decision: NOT READY for production as of 2026-10-02.**

## Evidence currently available

- Dashboard build and lint have been run locally for the current frontend
  changes.
- The repository contains authorization, policy, recovery, evidence, supply
  chain and cognitive-state implementation paths.
- Server-side approval resource binding and expiry regressions have been added.

## Blocking evidence

- Supported Python dependency environment has not run the full test suite in
  this checkout.
- No authorized staging evidence proves Jira → isolated executor → Evidence Pack
  → draft GitHub PR.
- No OIDC, PostgreSQL/Redis failure, object-storage signing, restore, load or
  alert-routing exercise has been observed.
- No independent security review, secret-rotation record, SBOM/vulnerability
  gate output or customer-managed executor isolation result is attached.

## Promotion gate

An accountable release owner must attach dated evidence for every blocking item
above, record deviations and owner/due date, and approve the resulting release
candidate. Until then, deployment must remain restricted to development or an
explicitly authorized non-production environment.
