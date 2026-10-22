#!/usr/bin/env bash
# Put the Homepage widget credentials into stacks/apps/secrets.sops.env. Goobs runs this on the
# Mac (it needs the lab SSH key and the age key in ~/.config/sops/age/keys.txt):
#
#     bash docs/homepage_keys.sh            # read the keys the apps already have, create the
#                                           # read-only tokens that don't exist yet, encrypt
#     bash docs/homepage_keys.sh --collect  # only read existing keys (changes nothing on the lab)
#
# Values never leave this script: they go straight from the VMs into the encrypted file. Also
# merges ~/.config/homelab/homepage-keys.env (HOMEPAGE_EMBY_KEY=... lines you add by hand) and
# ~/.config/homelab/igdb.env (IGDB_CLIENT_ID/SECRET, into the media stack) when they exist, and
# deletes them afterwards. Re-running is safe: every key is replaced, not duplicated.
set -euo pipefail
cd "$(dirname "$0")/.."

MEDIA=goobs@192.168.1.51 GPU=goobs@192.168.1.52 APPS=goobs@192.168.1.53
PVE=root@192.168.1.137 NAS=root@192.168.1.209
CREATE=1; [[ "${1:-}" == "--collect" ]] && CREATE=0
export SOPS_AGE_KEY_FILE="$HOME/.config/sops/age/keys.txt"

declare -A APPSV MEDIAV
rs() { ssh -o BatchMode=yes "$1" "sudo sh -c '$2'" 2>/dev/null | tr -d '\r' | head -1; }   # remote sudo, one line
need() { [[ -n "${2:-}" ]] && APPSV[$1]=$2 && echo "  $1: ok" || echo "  $1: NOT FOUND (left as is)"; }

echo "Reading existing keys..."
need HOMEPAGE_SONARR_KEY   "$(rs $MEDIA "sed -n \"s:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p\" /opt/appdata/sonarr/config.xml")"
need HOMEPAGE_RADARR_KEY   "$(rs $MEDIA "sed -n \"s:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p\" /opt/appdata/radarr/config.xml")"
need HOMEPAGE_PROWLARR_KEY "$(rs $MEDIA "sed -n \"s:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p\" /opt/appdata/prowlarr/config.xml")"
need HOMEPAGE_BAZARR_KEY   "$(rs $MEDIA "awk \"/^auth:/{f=1} f && /apikey:/{print \\\$2; exit}\" /opt/appdata/bazarr/config/config.yaml")"
need HOMEPAGE_SABNZBD_KEY  "$(rs $MEDIA "sed -n \"s/^api_key *= *//p\" /opt/appdata/sabnzbd/sabnzbd.ini")"
need HOMEPAGE_JELLYSEERR_KEY "$(rs $MEDIA "python3 -c \"import json;print(json.load(open(\\\"/opt/appdata/jellyseerr/settings.json\\\"))[\\\"main\\\"][\\\"apiKey\\\"])\"")"
need HOMEPAGE_TAUTULLI_KEY "$(rs $APPS "sed -n \"s/^api_key *= *//p\" /opt/appdata/tautulli/config.ini")"
need HOMEPAGE_PLEX_TOKEN   "$(rs $GPU "grep -o \"PlexOnlineToken=\\\"[^\\\"]*\\\"\" \"/opt/appdata/plex/Library/Application Support/Plex Media Server/Preferences.xml\" | cut -d\\\" -f2")"
# Audiobookshelf only has a token once the first-run admin exists.
need HOMEPAGE_AUDIOBOOKSHELF_KEY "$(rs $MEDIA "sqlite3 /opt/appdata/audiobookshelf/config/absdatabase.sqlite \"select token from users where type=\\\"root\\\" limit 1\" 2>/dev/null")"
APPSV[HOMEPAGE_QBITTORRENT_USER]=admin
APPSV[HOMEPAGE_QBITTORRENT_PASSWORD]=bypass   # the apps VM is on qBittorrent's auth-bypass list
APPSV[HOMEPAGE_PROXMOX_TOKEN_ID]='homepage@pve!homepage'

if (( CREATE )); then
  echo "Creating read-only tokens (skipped when they already exist)..."
  # Proxmox: cluster-wide user with the auditor role. Undo: pveum user delete homepage@pve
  if ! ssh $PVE pveum user list --output-format json 2>/dev/null | grep -q '"homepage@pve"'; then
    ssh $PVE 'pveum user add homepage@pve --comment "Homepage dashboard, read-only" && pveum acl modify / -user homepage@pve -role PVEAuditor' >/dev/null
    need HOMEPAGE_PROXMOX_TOKEN_SECRET "$(ssh $PVE 'pveum user token add homepage@pve homepage --privsep 0 --output-format json' | python3 -c 'import json,sys;print(json.load(sys.stdin)["value"])')"
  else echo "  Proxmox user homepage@pve exists; token left as is"; fi
  # TrueNAS API key (SCALE 23.10 keys are full access; the widget only reads). Undo: midclt call api_key.delete <id>
  if ! ssh $NAS 'midclt call api_key.query' 2>/dev/null | grep -q '"name": *"homepage"'; then
    need HOMEPAGE_TRUENAS_KEY "$(ssh $NAS "midclt call api_key.create '{\"name\":\"homepage\"}'" | python3 -c 'import json,sys;print(json.load(sys.stdin)["key"])')"
  else echo "  TrueNAS key homepage exists; left as is"; fi
  # Nextcloud serverinfo token (its only purpose is this read-only endpoint).
  nc=$(openssl rand -hex 32)
  ssh $APPS "sudo docker exec -u www-data apps-nextcloud-1 php occ config:app:set serverinfo token --value $nc" >/dev/null && need HOMEPAGE_NEXTCLOUD_TOKEN "$nc"
  # Paperless API token for the superuser (drf_create_token returns the existing one on re-runs).
  pu=$(rs $APPS "docker exec apps-paperless-1 python3 manage.py shell -c \"from django.contrib.auth.models import User; print(User.objects.filter(is_superuser=True).first().username)\"")
  need HOMEPAGE_PAPERLESS_TOKEN "$(rs $APPS "docker exec apps-paperless-1 python3 manage.py drf_create_token $pu | awk \"{print \\\$3}\"")"
fi

# Hand-added keys (Emby, Immich, RomM user/password) from the Mac.
if [[ -f ~/.config/homelab/homepage-keys.env ]]; then
  while IFS='=' read -r k v; do [[ $k == HOMEPAGE_* && -n $v ]] && APPSV[$k]=$v && echo "  $k: from homepage-keys.env"; done < ~/.config/homelab/homepage-keys.env
fi
if [[ -f ~/.config/homelab/igdb.env ]]; then
  while IFS='=' read -r k v; do [[ $k == IGDB_* && -n $v ]] && MEDIAV[$k]=$v && echo "  $k: from igdb.env"; done < ~/.config/homelab/igdb.env
fi

# Replace keys inside a sops dotenv file without leaving plaintext behind.
update_secrets() {  # file, assoc-array name
  local f=$1; local -n vals=$2; (( ${#vals[@]} )) || return 0
  local recips; recips=$(grep -o '^sops_age__list_[0-9]*__map_recipient=.*' "$f" | cut -d= -f2 | paste -sd, -)
  local plain; plain=$(mktemp "$HOME/.config/homelab/.secrets.XXXXXX"); chmod 600 "$plain"
  sops --decrypt --input-type dotenv --output-type dotenv "$f" > "$plain"
  for k in "${!vals[@]}"; do
    grep -q "^$k=" "$plain" && sed -i '' "s|^$k=.*|$k=${vals[$k]}|" "$plain" || echo "$k=${vals[$k]}" >> "$plain"
  done
  sops --encrypt --age "$recips" --input-type dotenv --output-type dotenv "$plain" > "$f"
  rm -P "$plain" 2>/dev/null || rm -f "$plain"
  echo "Updated $f (${#vals[@]} keys)"
}
update_secrets stacks/apps/secrets.sops.env APPSV
update_secrets stacks/media/secrets.sops.env MEDIAV
rm -f ~/.config/homelab/homepage-keys.env ~/.config/homelab/igdb.env

echo "Done. Now: git commit -am 'secrets: Homepage widget keys' && git push, then redeploy the apps stack"
echo "(ansible-playbook ansible/playbooks/site.yml --limit apps --tags stacks)."
