# Enterprise dashboard design system

## Product rule

The dashboard is a review and control surface, not a decorative status wall.
Every displayed state must come from an authorized API response. When a field
is absent or an optional endpoint is unavailable, the interface states that
fact instead of deriving a success state from adjacent data.

## Information hierarchy

1. **Mission Control** answers what is executing, what is blocked and what
   needs a person now.
2. **Run Detail** answers what changed: context, plan, approvals, execution
   timeline, diff, tests, evidence and draft PR.
3. **Evidence & Passports** answers whether a delivery can be traced from a
   requirement to an immutable snapshot and artifact.
4. **Operations** answers whether executors and jobs are healthy and gives
   authorized operators a deliberate recovery action.

## Components and states

| Component | Required states | Behaviour |
| --- | --- | --- |
| Status badge | `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `FAILED`, `BLOCKED`, `NOT RECORDED` | Text accompanies color; status is never color-only. |
| Approval row | Action, requester/time, rationale, binding digest, expiry | Approve/reject controls require rationale and disappear after decision. A new resource digest is a new approval. |
| Evidence card | Verified, unverified, missing, unavailable | Shows recorded verification status; does not render a green check from artifact existence. |
| Diff panel | Captured, empty, unavailable | Shows SHA-256 and changed-file count only when supplied by the API. |
| Passport | Complete, partial, stale, unavailable | Requirement/evidence/provenance/snapshot fields are independently rendered, including absence. |
| Destructive/recovery action | Ready, submitting, succeeded, failed | Requests a rationale where the endpoint supports it and refreshes authoritative state afterwards. |

## Accessibility and responsive baseline

Use semantic controls with visible labels, keyboard-focus styles, native
buttons for actions, `role="alert"` for submission errors, and text labels for
all status colors. The existing CSS collapses multi-column delivery/evidence
layouts on narrow screens; browser E2E must still verify focus order, keyboard
approval and 320 px-wide review of a pending approval.

## Copy rules

Use English product copy. Say **not recorded**, **unavailable**, or
**unverified** where appropriate. Reserve **verified**, **immutable**, and
**complete** for fields backed by an API record or cryptographic verification.
