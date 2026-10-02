# Enterprise runbooks

## Pending or expired approval

1. Open the Run Detail and confirm the action, resource digest, plan source
   hash and expiry match the intended delivery.
2. If the plan changed or status is `EXPIRED`, do not reuse the prior rationale.
   Review the new pending request and record a fresh decision.
3. Reject with a specific rationale when the resource is not safe. The decision
   remains in the run audit trail.
4. Escalate identity/authorization errors to the project administrator; do not
   bypass approval through a direct executor or integration call.

## Dead-lettered execution job

1. Confirm the job/run tenant and inspect its recorded failure, retry count and
   evidence state.
2. Check whether an external side effect may already have occurred before any
   requeue. For SCM, inspect the sandbox draft PR by run/evidence identifier.
3. Record the recovery rationale using the authorized Operations action, then
   requeue once.
4. Observe the new job lifecycle. If it fails again, stop automated retries and
   create an incident with run, job, trace and evidence identifiers.

## Evidence verification mismatch

1. Treat an unverified or mismatched Evidence Pack as a delivery blocker.
2. Preserve run, artifact hash, manifest hash and verification diagnostic; do
   not overwrite the original artifact.
3. Suspend the affected draft PR from promotion, investigate signing/key and
   storage provenance, then generate a new evidence record only after the
   underlying cause is fixed.

## Release rollback

1. Stop new execution intake and retain queues, evidence and audit storage.
2. Roll back only the application revision through the approved deployment
   process; never delete tenant data as a rollback shortcut.
3. Validate authorization, policy denial, queue lease and evidence read paths
   in a non-production canary before reopening intake.
4. Document incident timeline, affected run IDs, data-recovery outcome and
   follow-up owner.
