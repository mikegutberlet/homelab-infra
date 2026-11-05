#!/bin/bash
# Puts RomM's metadata-source keys into stacks/media/secrets.sops.env (asks for each, Enter keeps
# what is there). Goobs runs it on the Mac from the repo root: bash docs/romm_keys.sh
# Where the keys come from (all free):
#   IGDB (best metadata + covers): https://dev.twitch.tv/console/apps > Register Your Application:
#     Name "romm-<anything unique>", OAuth Redirect URL "http://localhost", Category "Application
#     Integration", Client Type "Confidential" > Manage > copy Client ID, "New Secret" for the secret.
#     (Twitch account with 2FA on; a name already taken fails silently.)
#   ScreenScraper (retro covers/videos): sign up at https://www.screenscraper.fr, use that login.
#   RetroAchievements: https://retroachievements.org/settings > "Web API Key".
#   SteamGridDB (cover search button): https://www.steamgriddb.com/profile/preferences/api.
# Then: cd ansible && ansible-playbook playbooks/site.yml --tags stacks --limit media ; git commit the env.
set -euo pipefail
FILE=stacks/media/secrets.sops.env
SOPS=~/.local/bin/sops
export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
KEYS=(IGDB_CLIENT_ID IGDB_CLIENT_SECRET SCREENSCRAPER_USER SCREENSCRAPER_PASSWORD RETROACHIEVEMENTS_API_KEY STEAMGRIDDB_API_KEY)

[ -x "$SOPS" ] || { echo "sops not found at $SOPS"; exit 1; }
tmp=$(mktemp -d); chmod 700 "$tmp"; trap 'rm -rf "$tmp"' EXIT
"$SOPS" --decrypt --input-type dotenv --output-type dotenv "$FILE" > "$tmp/plain.env"

for k in "${KEYS[@]}"; do
  cur=$(sed -n "s/^$k=//p" "$tmp/plain.env" | head -1)
  if [ -n "$cur" ]; then state="set, Enter keeps it"; else state="empty, Enter skips"; fi
  read -r -s -p "$k ($state): " val; echo
  [ -z "$val" ] && val="$cur"
  grep -v "^$k=" "$tmp/plain.env" > "$tmp/next.env" || true
  printf '%s=%s\n' "$k" "$val" >> "$tmp/next.env"
  mv "$tmp/next.env" "$tmp/plain.env"
done
# --filename-override: the temp file is outside stacks/, so .sops.yaml would match no rule.
"$SOPS" --encrypt --filename-override "$FILE" --input-type dotenv --output-type dotenv "$tmp/plain.env" > "$tmp/enc.env"
mv "$tmp/enc.env" "$FILE"
echo "Saved (encrypted) to $FILE. Next: cd ansible && ansible-playbook playbooks/site.yml --tags stacks --limit media"
