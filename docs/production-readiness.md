# Production Readiness

SACM is a secure and auditable software-delivery control plane for coding
agents. It governs identity, policy, approvals, durable runs, evidence, and
cost attribution; it does not replace agent frameworks or coding agents.

## Release gate

A release requires green lint, type checks, tests, package build, migration
validation, container build, CodeQL, and deployed HTTPS/OIDC smoke tests.
Completed delivery runs require policy-approved actions and evidence packs.

## Current deployment boundary

The Lightsail deployment is a single-node production pilot, not a
high-availability service. It has no database failover, autoscaling, rolling
deployment, or tested host-loss recovery. Do not claim HA or benchmark
superiority until the documented benchmark protocol and recovery/load tests
are published.
