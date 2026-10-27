#!/bin/bash
# Adds the two monitoring secrets to stacks/apps/secrets.sops.env without showing them:
#   PVE_EXPORTER_TOKEN      a new read-only Proxmox token prometheus@pve!prometheus (PVEAuditor)
#   GRAFANA_ADMIN_PASSWORD  a random password (Grafana's login form is off; Authentik signs in)
# Goobs runs it on the Mac from the repo root: bash docs/monitoring_secrets.sh
# Safe to run again: it replaces the token and both lines.
set -euo pipefail

PVE=root@192.168.1.37 # any cluster node; users and tokens are cluster-wide
FILE=stacks/apps/secrets.sops.env
SOPS=~/.local/bin/sops
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
AGE=$(awk '/age:/ {print $2}' .sops.yaml)

tmp=$(mktemp -d)
chmod 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT

echo "Creating the Proxmox token..."
token=$(ssh "$PVE" '
  pveum user list --output-format json | grep -q "\"prometheus@pve\"" ||
    pveum user add prometheus@pve --comment "Prometheus pve-exporter (read-only)" >/dev/null
  pveum acl modify / --users prometheus@pve --roles PVEAuditor
  pveum user token remove prometheus@pve prometheus >/dev/null 2>&1 || true
  pveum user token add prometheus@pve prometheus --privsep 0 --output-format json
' | python3 -c 'import json, sys; print(json.load(sys.stdin)["value"])')
[ -n "$token" ] || { echo "No token came back from Proxmox."; exit 1; }

password=$(openssl rand -base64 24 | tr -d '/+=')

echo "Updating $FILE..."
"$SOPS" --decrypt --input-type dotenv --output-type dotenv "$FILE" \
  | grep -v -E '^(PVE_EXPORTER_TOKEN|GRAFANA_ADMIN_PASSWORD)=' > "$tmp/plain.env"
printf 'PVE_EXPORTER_TOKEN=%s\nGRAFANA_ADMIN_PASSWORD=%s\n' "$token" "$password" >> "$tmp/plain.env"
"$SOPS" --encrypt --age "$AGE" --input-type dotenv --output-type dotenv "$tmp/plain.env" > "$tmp/enc.env"
mv "$tmp/enc.env" "$FILE"

echo "Done. Both secrets are saved, encrypted, in $FILE."
