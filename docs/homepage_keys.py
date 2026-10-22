#!/usr/bin/env python3
"""Put the Homepage widget credentials into stacks/apps/secrets.sops.env.

Goobs runs this on the Mac (needs the lab SSH key and the age key in ~/.config/sops/age/keys.txt):

    python3 docs/homepage_keys.py             # read existing keys, create the missing read-only
                                              # tokens, re-encrypt
    python3 docs/homepage_keys.py --collect   # only read existing keys (changes nothing on the lab)

Values go straight from the VMs into the encrypted file and are never printed. Also merges
~/.config/homelab/homepage-keys.env (HOMEPAGE_EMBY_KEY=... lines added by hand) and
~/.config/homelab/igdb.env (IGDB_CLIENT_ID/SECRET, into the media stack) when they exist, and
deletes them afterwards. Re-running is safe: every key is replaced, not duplicated.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile

MEDIA, GPU, APPS = "goobs@192.168.1.51", "goobs@192.168.1.52", "goobs@192.168.1.53"
PVE, NAS = "root@192.168.1.137", "root@192.168.1.209"
CREATE = "--collect" not in sys.argv
HOME = os.path.expanduser("~")
os.environ["SOPS_AGE_KEY_FILE"] = f"{HOME}/.config/sops/age/keys.txt"
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

apps_vals, media_vals = {}, {}


def ssh(host, cmd, sudo=True):
    """Run one command on a lab machine; returns its first output line ('' on failure)."""
    full = f"sudo sh -c {json.dumps(cmd)}" if sudo else cmd
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, full], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""


def need(name, value, store=apps_vals):
    if value:
        store[name] = value
        print(f"  {name}: ok")
    else:
        print(f"  {name}: NOT FOUND (left as is)")


print("Reading existing keys...")
for app in ("sonarr", "radarr", "prowlarr"):
    need(f"HOMEPAGE_{app.upper()}_KEY",
         ssh(MEDIA, f"sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /opt/appdata/{app}/config.xml"))
need("HOMEPAGE_BAZARR_KEY",
     ssh(MEDIA, "awk '/^auth:/{f=1} f && /apikey:/{print $2; exit}' /opt/appdata/bazarr/config/config.yaml"))
need("HOMEPAGE_SABNZBD_KEY", ssh(MEDIA, "sed -n 's/^api_key *= *//p' /opt/appdata/sabnzbd/sabnzbd.ini"))
need("HOMEPAGE_JELLYSEERR_KEY",
     ssh(MEDIA, "python3 -c 'import json;print(json.load(open(\"/opt/appdata/jellyseerr/settings.json\"))[\"main\"][\"apiKey\"])'"))
need("HOMEPAGE_TAUTULLI_KEY", ssh(APPS, "sed -n 's/^api_key *= *//p' /opt/appdata/tautulli/config.ini"))
need("HOMEPAGE_PLEX_TOKEN",
     ssh(GPU, "grep -o 'PlexOnlineToken=\"[^\"]*\"' '/opt/appdata/plex/Library/Application Support/Plex Media Server/Preferences.xml' | cut -d'\"' -f2"))
# Audiobookshelf only has a token once the first-run admin exists.
need("HOMEPAGE_AUDIOBOOKSHELF_KEY",
     ssh(MEDIA, "sqlite3 /opt/appdata/audiobookshelf/config/absdatabase.sqlite \"select token from users where type='root' limit 1\""))
apps_vals["HOMEPAGE_QBITTORRENT_USER"] = "admin"
apps_vals["HOMEPAGE_QBITTORRENT_PASSWORD"] = "bypass"  # the apps VM is on qBittorrent's auth-bypass list
apps_vals["HOMEPAGE_PROXMOX_TOKEN_ID"] = "homepage@pve!homepage"

if CREATE:
    print("Creating read-only tokens (skipped when they already exist)...")
    # Proxmox: cluster-wide user with the auditor role. Undo: pveum user delete homepage@pve
    users = subprocess.run(["ssh", PVE, "pveum user list --output-format json"], capture_output=True, text=True).stdout
    if '"homepage@pve"' not in users:
        subprocess.run(["ssh", PVE, 'pveum user add homepage@pve --comment "Homepage dashboard, read-only" '
                        "&& pveum acl modify / -user homepage@pve -role PVEAuditor"], check=True, capture_output=True)
        out = subprocess.run(["ssh", PVE, "pveum user token add homepage@pve homepage --privsep 0 --output-format json"],
                             capture_output=True, text=True).stdout
        need("HOMEPAGE_PROXMOX_TOKEN_SECRET", json.loads(out).get("value", "") if out.strip() else "")
    else:
        print("  Proxmox user homepage@pve exists; token left as is")
    # TrueNAS API key (SCALE 23.10 keys are full access; the widget only reads). Undo: midclt call api_key.delete <id>
    keys = subprocess.run(["ssh", NAS, "midclt call api_key.query"], capture_output=True, text=True).stdout
    if not re.search(r'"name":\s*"homepage"', keys):
        out = subprocess.run(["ssh", NAS, "midclt call api_key.create '{\"name\":\"homepage\"}'"],
                             capture_output=True, text=True).stdout
        need("HOMEPAGE_TRUENAS_KEY", json.loads(out).get("key", "") if out.strip() else "")
    else:
        print("  TrueNAS key homepage exists; left as is")
    # Nextcloud serverinfo token (its only purpose is this read-only endpoint).
    nc = secrets.token_hex(32)
    r = subprocess.run(["ssh", APPS, f"sudo docker exec -u www-data apps-nextcloud-1 php occ config:app:set serverinfo token --value {nc}"],
                       capture_output=True, text=True)
    need("HOMEPAGE_NEXTCLOUD_TOKEN", nc if r.returncode == 0 else "")
    # Paperless API token for the superuser (drf_create_token returns the existing one on re-runs).
    user = ssh(APPS, "docker exec apps-paperless-1 python3 manage.py shell -c "
                     "'from django.contrib.auth.models import User; print(User.objects.filter(is_superuser=True).first().username)'")
    tok = ssh(APPS, f"docker exec apps-paperless-1 python3 manage.py drf_create_token {user}") if user else ""
    need("HOMEPAGE_PAPERLESS_TOKEN", tok.split()[-1] if tok else "")
    # qBittorrent: let the apps VM (Homepage) read the queue without a login. Done from inside
    # qBittorrent's own network namespace, where localhost needs no password. No restart.
    prefs = json.dumps({"bypass_auth_subnet_whitelist_enabled": True, "bypass_auth_subnet_whitelist": "192.168.1.53/32"})
    r = subprocess.run(["ssh", MEDIA, "sudo docker exec media-qbittorrent-1 curl -s --data-urlencode "
                        + json.dumps("json=" + prefs) + " http://127.0.0.1:8085/api/v2/app/setPreferences"],
                       capture_output=True, text=True)
    print("  qBittorrent: 192.168.1.53 on the auth-bypass list" if r.returncode == 0 else "  qBittorrent: FAILED")

# Hand-added keys from the Mac (Emby, Immich, RomM user/password; IGDB for the media stack).
for path, prefix, store in ((f"{HOME}/.config/homelab/homepage-keys.env", "HOMEPAGE_", apps_vals),
                            (f"{HOME}/.config/homelab/igdb.env", "IGDB_", media_vals)):
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k.startswith(prefix) and v:
                store[k] = v
                print(f"  {k}: from {os.path.basename(path)}")


def update_secrets(path, vals):
    """Replace keys inside a sops dotenv file; the plaintext never touches the repo."""
    if not vals:
        return
    enc = open(path).read()
    recips = ",".join(re.findall(r"^sops_age__list_\d+__map_recipient=(.*)$", enc, re.M))
    plain = subprocess.run(["sops", "--decrypt", "--input-type", "dotenv", "--output-type", "dotenv", path],
                           capture_output=True, text=True, check=True).stdout
    lines = [ln for ln in plain.splitlines() if ln.partition("=")[0] not in vals]
    lines += [f"{k}={v}" for k, v in vals.items()]
    fd, tmp = tempfile.mkstemp(prefix=".secrets.", dir=f"{HOME}/.config/homelab")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        out = subprocess.run(["sops", "--encrypt", "--age", recips, "--input-type", "dotenv", "--output-type", "dotenv", tmp],
                             capture_output=True, text=True, check=True).stdout
        open(path, "w").write(out)
    finally:
        os.remove(tmp)
    print(f"Updated {path} ({len(vals)} keys)")


os.makedirs(f"{HOME}/.config/homelab", exist_ok=True)
update_secrets("stacks/apps/secrets.sops.env", apps_vals)
update_secrets("stacks/media/secrets.sops.env", media_vals)
for p in (f"{HOME}/.config/homelab/homepage-keys.env", f"{HOME}/.config/homelab/igdb.env"):
    if os.path.exists(p):
        os.remove(p)

print("Done. Now: git commit -am 'secrets: Homepage widget keys' && git push, then redeploy the apps stack")
print("(ansible-playbook ansible/playbooks/site.yml --limit apps --tags stacks).")
