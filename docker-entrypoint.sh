#!/bin/sh
set -eu

if [ -n "${SACM_DATABASE_PASSWORD_FILE:-}" ]; then
  database_password=$(cat "$SACM_DATABASE_PASSWORD_FILE")
  export DATABASE_URL="postgresql+psycopg2://${SACM_DATABASE_USER:?}:${database_password}@${SACM_DATABASE_HOST:?}:${SACM_DATABASE_PORT:-5432}/${SACM_DATABASE_NAME:?}"
  unset database_password
fi

if [ -n "${SACM_EVIDENCE_HMAC_KEY_FILE:-}" ]; then
  export SACM_EVIDENCE_HMAC_KEY=$(cat "$SACM_EVIDENCE_HMAC_KEY_FILE")
fi

if [ -n "${SACM_GITHUB_WEBHOOK_SECRET_FILE:-}" ]; then
  export SACM_GITHUB_WEBHOOK_SECRET=$(cat "$SACM_GITHUB_WEBHOOK_SECRET_FILE")
fi

exec "$@"
