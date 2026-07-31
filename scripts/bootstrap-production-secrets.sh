#!/bin/sh
set -eu

secrets_dir=${1:-secrets}
umask 077
mkdir -p "$secrets_dir"

create_secret() {
  name=$1
  path="$secrets_dir/$name"
  if [ ! -f "$path" ]; then
    openssl rand -hex 32 > "$path"
  fi
}

create_secret postgres_password
create_secret evidence_hmac_key
create_secret github_webhook_secret

evidence_signing_key="$secrets_dir/evidence_signing_private_key"
if [ ! -f "$evidence_signing_key" ]; then
  openssl genpkey -algorithm ED25519 -out "$evidence_signing_key"
fi

job_signing_key="$secrets_dir/job_signing_private_key"
if [ ! -f "$job_signing_key" ]; then
  openssl genpkey -algorithm ED25519 -out "$job_signing_key"
fi

printf 'Created missing secrets in %s; existing secrets were not changed.\n' "$secrets_dir"
