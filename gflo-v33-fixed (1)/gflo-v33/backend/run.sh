#!/bin/sh
# Local dev server.
#
# uvicorn[standard] trusts X-Forwarded-For from 127.0.0.1 by DEFAULT and rewrites
# the client address from it, which would let a direct client spoof its IP and
# walk past the login rate limiter. We turn that off and let the app decide,
# using TRUSTED_PROXIES (see app/security.py). Set FORWARDED_ALLOW_IPS to your
# real proxy only when you genuinely run behind one.
set -e
cd "$(dirname "$0")"
PROXY_FLAG="--no-proxy-headers"
if [ -n "$FORWARDED_ALLOW_IPS" ]; then
  PROXY_FLAG="--proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS"
fi
exec uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" $PROXY_FLAG "$@"
