#!/bin/bash
# Hermes Agent secrets (stacks/apps/compose.yaml, services hermes-init and hermes).
# Goobs runs it on the Mac from the repo root: bash docs/hermes_setup.sh
# It asks, with hidden prompts, for the Anthropic key, the GitHub token and the Telegram bot token,
# and visibly for the numeric Telegram user id; makes the read-only inspect SSH key pair at
# ~/.config/homelab/hermes_inspect_ed25519 (the ops play installs its .pub on every VM); writes all of
# it into stacks/apps/secrets.sops.env with sops. Safe to run again: an empty answer keeps the value
# already in the file, and an existing key pair is kept.
set -euo pipefail

FILE=stacks/apps/secrets.sops.env
SOPS=~/.local/bin/sops
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
KEY=~/.config/homelab/hermes_inspect_ed25519

[ -x "$SOPS" ] || { echo "sops not found at $SOPS"; exit 1; }
[ -f "$FILE" ] || { echo "run from the repo root ($FILE not found)"; exit 1; }

mkdir -p ~/.config/homelab
if [ ! -f "$KEY" ]; then
  ssh-keygen -q -t ed25519 -N '' -C hermes-inspect -f "$KEY"
  echo "New inspect key pair at $KEY (run the ops play so every VM accepts it)."
fi

tmp=$(mktemp -d)
chmod 700 "$tmp"
trap 'rm -rf "$tmp"' EXIT
"$SOPS" --decrypt --input-type dotenv --output-type dotenv "$FILE" > "$tmp/plain.env"

# set NAME VALUE: replace or append one line in the decrypted file; empty VALUE keeps the old line.
set_var() {
  if [ -n "$2" ]; then
    grep -v "^$1=" "$tmp/plain.env" > "$tmp/next.env" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp/next.env"
    mv "$tmp/next.env" "$tmp/plain.env"
  elif ! grep -q "^$1=.\+" "$tmp/plain.env"; then
    echo "$1 is empty and nothing was entered; fill it before deploying." >&2
  fi
}

printf 'Anthropic API key for Hermes (console.anthropic.com, key named hermes; blank keeps current): '
read -rs v; echo; set_var HERMES_ANTHROPIC_API_KEY "$v"
printf 'GitHub fine-grained token for homelab-infra (blank keeps current): '
read -rs v; echo; set_var HERMES_GITHUB_TOKEN "$v"
printf 'Telegram bot token from @BotFather (blank keeps current): '
read -rs v; echo; set_var HERMES_TELEGRAM_BOT_TOKEN "$v"
printf 'Your numeric Telegram user id (ask @userinfobot; blank keeps current): '
read -r v; set_var HERMES_TELEGRAM_USER_ID "$v"
set_var HERMES_INSPECT_KEY "$(base64 < "$KEY" | tr -d '\n')"

# --filename-override: the temp file is outside stacks/, so .sops.yaml would match no rule.
"$SOPS" --encrypt --filename-override "$FILE" --input-type dotenv --output-type dotenv "$tmp/plain.env" > "$tmp/enc.env"
mv "$tmp/enc.env" "$FILE"
echo "Saved, encrypted, in $FILE."
echo "Next: cd ansible && ansible-playbook playbooks/site.yml --tags ops --limit docker_vms && ansible-playbook playbooks/site.yml --tags stacks --limit apps; then commit $FILE."
