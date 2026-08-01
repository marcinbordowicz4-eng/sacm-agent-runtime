#!/bin/sh
set -eu

bucket=${SACM_SITE_BUCKET:?Set SACM_SITE_BUCKET}
distribution=${SACM_CLOUDFRONT_DISTRIBUTION_ID:?Set SACM_CLOUDFRONT_DISTRIBUTION_ID}
api_url=${VITE_SACM_API_URL:-https://api.sacm.io}

VITE_SACM_API_URL="$api_url" npm --prefix apps/dashboard run build

aws s3 sync apps/dashboard/dist "s3://${bucket}" \
  --delete \
  --exclude "index.html" \
  --cache-control "public,max-age=31536000,immutable"

aws s3 cp apps/dashboard/dist/index.html "s3://${bucket}/index.html" \
  --cache-control "no-cache,no-store,must-revalidate" \
  --content-type "text/html; charset=utf-8"

aws cloudfront create-invalidation \
  --distribution-id "$distribution" \
  --paths "/*" >/dev/null

printf 'Published https://sacm.io via s3://%s\n' "$bucket"
