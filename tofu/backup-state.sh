#!/bin/sh
# Encrypted copy of the OpenTofu state for git (GitHub is the off-site copy). The state itself
# (terraform.tfstate) only exists on the Mac. Run after every `tofu apply`, then commit
# tofu/terraform.tfstate.sops.json. Restore:
#   ~/.local/bin/sops --decrypt --input-type json --output-type binary \
#     tofu/terraform.tfstate.sops.json > tofu/terraform.tfstate
set -eu
cd "$(dirname "$0")/.."
SOPS="$HOME/.local/bin/sops"
export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}"
"$SOPS" --encrypt --input-type binary --output-type json tofu/terraform.tfstate > tofu/terraform.tfstate.sops.json.new
# Prove it decrypts back to the same bytes before replacing the last good copy.
"$SOPS" --decrypt --input-type json --output-type binary tofu/terraform.tfstate.sops.json.new | cmp -s - tofu/terraform.tfstate
mv tofu/terraform.tfstate.sops.json.new tofu/terraform.tfstate.sops.json
echo "tofu/terraform.tfstate.sops.json updated and verified"
