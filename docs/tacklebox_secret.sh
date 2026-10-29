#!/bin/bash
# First run of the Tackle Box archive (stacks/apps/compose.yaml, service tackle-box-archive):
#   1. creates its NAS folder /mnt/nfs/Documents/TackleBox as the storage user (the export
#      squashes root, so Compose cannot create it and the stacks play fails on "permission denied");
#   2. puts a random TACKLEBOX_CORPUS_TOKEN into stacks/apps/secrets.sops.env and keeps a copy in
#      ~/.config/homelab/tacklebox-token (mode 600) for the fishing app's settings.
# Goobs runs it on the Mac from the repo root: bash docs/tacklebox_secret.sh
# Safe to run again: it keeps an existing token and only creates what is missing.
set -euo pipefail

APPS=goobs@192.168.1.53
KEY=~/.ssh/homelab_inventory_ed25519
FILE=stacks/apps/secrets.sops.env
SOPS=~/.local/bin/sops
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
COPY=~/.config/homelab/tacklebox-token

echo "Creating /mnt/nfs/Documents/TackleBox on the NAS (as storage)..."
ssh -i "$KEY" -o BatchMode=yes "$APPS" 'sudo -n -u storage mkdir -p /mnt/nfs/Documents/TackleBox && ls -ld /mnt/nfs/Documents/TackleBox'

tmp=$(mktemp -d)
chmod 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT

"$SOPS" --decrypt --input-type dotenv --output-type dotenv "$FILE" > "$tmp/plain.env"
if grep -q '^TACKLEBOX_CORPUS_TOKEN=.\+' "$tmp/plain.env"; then
  echo "TACKLEBOX_CORPUS_TOKEN already set in $FILE; keeping it."
  token=$(sed -n 's/^TACKLEBOX_CORPUS_TOKEN=//p' "$tmp/plain.env")
else
  token=$(openssl rand -hex 24)
  grep -v '^TACKLEBOX_CORPUS_TOKEN=' "$tmp/plain.env" > "$tmp/plain2.env" || true
  printf 'TACKLEBOX_CORPUS_TOKEN=%s\n' "$token" >> "$tmp/plain2.env"
  # --filename-override: the temp file is outside stacks/, so .sops.yaml would match no rule.
  "$SOPS" --encrypt --filename-override "$FILE" --input-type dotenv --output-type dotenv "$tmp/plain2.env" > "$tmp/enc.env"
  mv "$tmp/enc.env" "$FILE"
  echo "Token saved, encrypted, in $FILE."
fi
mkdir -p "$(dirname "$COPY")"
(umask 077; printf '%s\n' "$token" > "$COPY")
echo "A copy for the fishing app's settings is in $COPY (not shown here)."
echo "Next: cd ansible && ansible-playbook playbooks/site.yml --tags stacks --limit apps, then commit $FILE."
