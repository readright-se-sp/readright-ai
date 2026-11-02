#!/usr/bin/env bash
# Run every 5 minutes as root (root crontab or a root systemd timer: it calls `shutdown`).
# Shuts the instance down when the server has had no request for IDLE_MINUTES. Does nothing when
# /health/live is unreachable. The check goes through Caddy (only Caddy publishes ports); set
# MAI_SITE_ADDRESS in the timer's environment to use the site name, or HEALTH_URL to override.
set -euo pipefail

URL="${HEALTH_URL:-https://localhost/health/live}"
LIMIT_MINUTES="${IDLE_MINUTES:-30}"
CURL_ARGS=(-fsS -k --max-time 5)
if [ -z "${HEALTH_URL:-}" ] && [ -n "${MAI_SITE_ADDRESS:-}" ]; then
  URL="https://${MAI_SITE_ADDRESS}/health/live"
  # Caddy picks the certificate by SNI, so keep the site name and connect to this host.
  CURL_ARGS+=(--resolve "${MAI_SITE_ADDRESS}:443:127.0.0.1")
fi

body="$(curl "${CURL_ARGS[@]}" "$URL")" || {
  echo "idle-check: $URL unreachable, doing nothing"
  exit 0
}

since="$(printf '%s' "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d.get("last_request_at") or d.get("started_at") or "")
')" || {
  echo "idle-check: could not parse the /health/live response, doing nothing"
  exit 0
}
if [ -z "$since" ]; then
  echo "idle-check: no timestamp in response, doing nothing"
  exit 0
fi

since_epoch="$(date -d "$since" +%s)" || {
  echo "idle-check: could not parse timestamp '$since', doing nothing"
  exit 0
}
idle_minutes=$(( ($(date +%s) - since_epoch) / 60 ))
echo "idle-check: idle for ${idle_minutes} min (limit ${LIMIT_MINUTES})"
if [ "$idle_minutes" -gt "$LIMIT_MINUTES" ]; then
  shutdown -h now
fi
