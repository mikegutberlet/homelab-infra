#!/bin/sh
# Keep the MyAnonamouse session's IP equal to the VPN's current IP (docs/books.md, "MyAnonamouse").
# Runs inside gluetun's network, so the caller's IP is PIA's. MAM answers JSON: "Completed" when the
# IP was moved, "No change" when it already matched, an error when the session was not created with
# "allow session to set dynamic seedbox IP" or has been called again within the hour.
set -u
JAR=/data/cookies.txt
if [ ! -s "$JAR" ]; then # first run: seed the cookie jar from MAM_ID; MAM may rotate it later
  printf '.myanonamouse.net\tTRUE\t/\tTRUE\t0\tmam_id\t%s\n' "$MAM_ID" > "$JAR"
fi
while :; do
  out=$(curl -sS -m 30 -b "$JAR" -c "$JAR" https://t.myanonamouse.net/json/dynamicSeedbox.php 2>&1)
  echo "$(date '+%F %T') $out"
  [ "${ONCE:-false}" = "true" ] && exit 0
  sleep "${INTERVAL_MIN:-60}m"
done
