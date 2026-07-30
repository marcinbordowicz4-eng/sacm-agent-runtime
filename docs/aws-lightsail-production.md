# AWS Lightsail production deployment

## Current test environment

The example deployment uses one Lightsail Xlarge instance in `eu-central-1`
with 16 GB RAM, 4 vCPU, 320 GB SSD storage, and 4 GB swap. Its 84 USD/month
bundle costs approximately 19.4 USD for seven days when billed hourly. It is
intended for a short, single-node performance test, not high availability.

The earlier Small and Large test instances were terminated before this host
was created. The static IP was retained and moved to the Xlarge instance.

## Prerequisites

1. Create DNS `A` records for `sacm.io` and any webhook hostname used by the
   service, pointing to the Lightsail static IP.
2. Enable IAM Identity Center in the AWS console. Create a customer-managed
   OAuth 2.0 application whose audience is `sacm-api`, then assign the
   required users or groups.
3. Set `SACM_OIDC_ISSUER`, `SACM_OIDC_AUDIENCE`, and, if needed,
   `SACM_OIDC_JWKS_URL` in `production.env`. The issuer must exactly match
   token `iss`.
4. Set a monitored mailbox in `ACME_EMAIL`.

IAM Identity Center is configured in its home Region, which can differ from
the Lightsail Region. Do not set `SACM_AUTH_REQUIRED=false` to bypass this
requirement.

## Initial server deployment

Copy this repository to `/opt/sacm`, then run:

```bash
cd /opt/sacm
cp production.env.example production.env
editor production.env
sh scripts/bootstrap-production-secrets.sh /opt/sacm/secrets
mkdir -p /opt/sacm/repositories
docker compose --env-file production.env -f docker-compose.production.yml up -d --build
```

The API starts only after its packaged database migration completes. Traefik
obtains and renews the certificate automatically after DNS propagation.

## Backup and recovery

Take a Lightsail snapshot before each release and at least daily while the
service holds production data. Test restoration into a separate instance
before relying on backups. The snapshot price is not included in the 12
USD/month instance price, so retain only the minimum snapshots that satisfy
the recovery objective.

To restore, create a replacement instance from the tested snapshot, attach
the static IP only after validation, and then run the deployment command
above. Rotate all files under `/opt/sacm/secrets` after a suspected compromise.

## Cost guardrails

- Terminate the `xlarge_3_0` instance within seven days to remain within the
  20 USD test budget.
- Do not add a managed database, load balancer, NAT gateway, or Route 53
  health check under this budget.
- Review AWS Bills monthly and configure a 15 USD AWS Budget alert in the
  account billing console.
