#!/bin/sh
# Keep the MyAnonamouse session's IP equal to the VPN's current IP (docs/books.md, "MyAnonamouse").
# Runs inside gluetun's network, so the caller's IP is PIA's. MAM answers JSON: "Completed" when the
# IP was moved, "No change" when it already matched, an error when the session was not created with
# "allow session to set dynamic seedbox IP" or has been called again within the hour.
set -u
JAR=/data/cookies.txt
while :; do
  # Seed the jar from MAM_ID while it holds no mam_id (first run, or the runs before the secret was
  # set left an empty one behind); afterwards the jar wins, because MAM may rotate the id.
  if ! grep -q "mam_id	." "$JAR" 2>/dev/null; then
    if [ -z "${MAM_ID:-}" ]; then
      echo "$(date '+%F %T') MAM_ID is empty: no MyAnonamouse session yet (docs/books.md)"
      sleep "${INTERVAL_MIN:-60}m"
      continue
    fi
    printf '.myanonamouse.net\tTRUE\t/\tTRUE\t0\tmam_id\t%s\n' "$MAM_ID" > "$JAR"
  fi
  out=$(curl -sS -m 30 -b "$JAR" -c "$JAR" https://t.myanonamouse.net/json/dynamicSeedbox.php 2>&1)
  echo "$(date '+%F %T') $out"
  [ "${ONCE:-false}" = "true" ] && exit 0
  sleep "${INTERVAL_MIN:-60}m"
done
