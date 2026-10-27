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
import shutil
import subprocess
import sys

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
need("HOMEPAGE_BAZARR_KEY",  # the apikey line inside the auth: block (no $ here: sh -c would expand it)
     ssh(MEDIA, "sed -n '/^auth:/,/^[^ ]/{s/^ *apikey: *//p;}' /opt/appdata/bazarr/config/config.yaml").strip("'\""))
need("HOMEPAGE_SABNZBD_KEY", ssh(MEDIA, "sed -n 's/^api_key *= *//p' /opt/appdata/sabnzbd/sabnzbd.ini"))
need("HOMEPAGE_JELLYSEERR_KEY",
     ssh(MEDIA, "python3 -c 'import json;print(json.load(open(\"/opt/appdata/jellyseerr/settings.json\"))[\"main\"][\"apiKey\"])'"))
need("HOMEPAGE_TAUTULLI_KEY", ssh(APPS, "sed -n 's/^api_key *= *//p' /opt/appdata/tautulli/config.ini"))
need("HOMEPAGE_PLEX_TOKEN",
     ssh(GPU, "grep -o 'PlexOnlineToken=\"[^\"]*\"' '/opt/appdata/plex/Library/Application Support/Plex Media Server/Preferences.xml' | cut -d'\"' -f2"))
# Audiobookshelf only has a token once the first-run admin exists. Read through the container's
# own sqlite (the VM's sqlite3 is too old for the database's triggers).
abs_js = ('const s=require("sqlite3");const db=new s.Database("/config/absdatabase.sqlite",s.OPEN_READONLY);'
          'db.get("select token from users where type=?",["root"],(e,r)=>console.log(r?r.token:""))')
r = subprocess.run(["ssh", "-o", "BatchMode=yes", MEDIA, f"sudo docker exec -w /app media-audiobookshelf-1 node -e '{abs_js}'"],
                   capture_output=True, text=True)
need("HOMEPAGE_AUDIOBOOKSHELF_KEY", r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else "")
apps_vals["HOMEPAGE_QBITTORRENT_USER"] = "admin"
apps_vals["HOMEPAGE_QBITTORRENT_PASSWORD"] = "bypass"  # the apps VM is on qBittorrent's auth-bypass list
apps_vals["HOMEPAGE_PROXMOX_TOKEN_ID"] = "homepage@pve!homepage"

if CREATE:
    print("Creating read-only tokens (skipped when they already exist)...")
    # Proxmox: cluster-wide user with the auditor role. Undo: pveum user delete homepage@pve
    # The token secret is shown once, at creation, so an existing token is replaced.
    users = subprocess.run(["ssh", PVE, "pveum user list --output-format json"], capture_output=True, text=True).stdout
    if '"homepage@pve"' not in users:
        subprocess.run(["ssh", PVE, 'pveum user add homepage@pve --comment "Homepage dashboard, read-only" '
                        "&& pveum acl modify / -user homepage@pve -role PVEAuditor"], check=True, capture_output=True)
    subprocess.run(["ssh", PVE, "pveum user token remove homepage@pve homepage"], capture_output=True)
    out = subprocess.run(["ssh", PVE, "pveum user token add homepage@pve homepage --privsep 0 --output-format json"],
                         capture_output=True, text=True).stdout
    need("HOMEPAGE_PROXMOX_TOKEN_SECRET", json.loads(out).get("value", "") if out.strip() else "")
    # TrueNAS API key (owned by root; the widget only reads). Undo: midclt call api_key.delete <id>
    # Also shown once, so an existing "homepage" key is deleted and made again.
    keys = subprocess.run(["ssh", NAS, "midclt call api_key.query"], capture_output=True, text=True).stdout
    for k in (json.loads(keys) if keys.strip() else []):
        if k.get("name") == "homepage":
            subprocess.run(["ssh", NAS, f"midclt call api_key.delete {k['id']}"], capture_output=True)
    # SCALE 24.10+ keys belong to a user (username required); on 23.10 a key carries an
    # allowlist instead, and an empty one denies everything (the 403s of the first run).
    out = ""
    for body in ('{"name":"homepage","username":"root"}',
                 '{"name":"homepage","allowlist":[{"method":"GET","resource":"*"}]}'):
        r = subprocess.run(["ssh", NAS, f"midclt call api_key.create '{body}'"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            out = r.stdout
            break
    need("HOMEPAGE_TRUENAS_KEY", json.loads(out).get("key", "") if out.strip() else "")
    # Nextcloud serverinfo token (its only purpose is this read-only endpoint).
    nc = secrets.token_hex(32)
    r = subprocess.run(["ssh", APPS, f"sudo docker exec -u www-data apps-nextcloud-1 php occ config:app:set serverinfo token --value {nc}"],
                       capture_output=True, text=True)
    need("HOMEPAGE_NEXTCLOUD_TOKEN", nc if r.returncode == 0 else "")
    # Paperless API token for the superuser (get_or_create: the same token on every run).
    pl = ("from rest_framework.authtoken.models import Token; from django.contrib.auth.models import User; "
          "t,_=Token.objects.get_or_create(user=User.objects.filter(is_superuser=True).first()); print('TOKEN='+t.key)")
    r = subprocess.run(["ssh", APPS, f'sudo docker exec apps-paperless-1 python3 manage.py shell -c "{pl}"'],
                       capture_output=True, text=True)
    m = re.search(r"TOKEN=(\S+)", r.stdout)
    need("HOMEPAGE_PAPERLESS_TOKEN", m.group(1) if m else "")
    # qBittorrent: let the apps VM (Homepage) read the queue without a login. Done from inside
    # qBittorrent's own network namespace, where localhost needs no password. No restart.
    prefs = json.dumps({"bypass_auth_subnet_whitelist_enabled": True, "bypass_auth_subnet_whitelist": "192.168.1.53/32"})
    r = subprocess.run(["ssh", MEDIA, "sudo docker exec media-qbittorrent-1 curl -s --data-urlencode "
                        + json.dumps("json=" + prefs) + " http://127.0.0.1:8085/api/v2/app/setPreferences"],
                       capture_output=True, text=True)
    print("  qBittorrent: 192.168.1.53 on the auth-bypass list" if r.returncode == 0 else "  qBittorrent: FAILED")

# Hand-added keys from the Mac (Emby, Immich, Tdarr, RomM user/password; IGDB for the media stack).
for path, prefix, store in ((f"{HOME}/.config/homelab/homepage-keys.env", "HOMEPAGE_", apps_vals),
                            (f"{HOME}/.config/homelab/igdb.env", "IGDB_", media_vals)):
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k.startswith(prefix) and v:
                store[k] = v
                print(f"  {k}: from {os.path.basename(path)}")


SOPS = next((p for p in [shutil.which("sops"), "/usr/local/bin/sops", "/opt/homebrew/bin/sops",
                         f"{HOME}/.local/bin/sops", f"{HOME}/go/bin/sops"] if p and os.path.exists(p)), None)


def sops(args, stdin):
    """Run sops on the file given on stdin: locally when installed, else on the apps VM, which has
    sops and the age key (/root/.config/sops/age/keys.txt) for the Ansible deploys. Nothing is
    written to disk either way."""
    if SOPS:
        cmd = [SOPS, *args, "/dev/stdin"]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", APPS,
               "sudo SOPS_AGE_KEY_FILE=/root/.config/sops/age/keys.txt sops " + " ".join(args) + " /dev/stdin"]
    # cwd "/" keeps sops from finding the repo's .sops.yaml, whose creation rules match only
    # the real file path, not stdin (the recipient is passed on the command line instead).
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd="/")
    if r.returncode:
        sys.exit(f"sops failed ({'local' if SOPS else 'on the apps VM'}): {r.stderr.strip()[-300:]}")
    return r.stdout


def dotenv_value(v):
    """Quote a value for a compose .env file: a bare "$" there starts a variable reference, so a
    password like pa$sword reaches the container truncated (and compose warns 'variable "sword"
    is not set'). Single quotes keep it literal; a value with a quote of its own escapes the $."""
    if v.startswith(("'", '"')) or "$" not in v:
        return v
    return f"'{v}'" if "'" not in v else v.replace("$", "$$")


def update_secrets(path, vals):
    """Replace keys inside a sops dotenv file; the plaintext never touches the repo or a disk."""
    if not vals:
        return
    enc = open(path).read()
    recips = ",".join(re.findall(r"^sops_age__list_\d+__map_recipient=(.*)$", enc, re.M))
    plain = sops(["--decrypt", "--input-type", "dotenv", "--output-type", "dotenv"], enc)
    lines = [ln for ln in plain.splitlines() if ln.partition("=")[0] not in vals]
    lines += [f"{k}={dotenv_value(v)}" for k, v in vals.items()]
    out = sops(["--encrypt", "--age", recips, "--input-type", "dotenv", "--output-type", "dotenv"], "\n".join(lines) + "\n")
    open(path, "w").write(out)
    print(f"Updated {path} ({len(vals)} keys)")


update_secrets("stacks/apps/secrets.sops.env", apps_vals)
update_secrets("stacks/media/secrets.sops.env", media_vals)
for p in (f"{HOME}/.config/homelab/homepage-keys.env", f"{HOME}/.config/homelab/igdb.env"):
    if os.path.exists(p):
        os.remove(p)

print("Done. Now: git commit -am 'secrets: Homepage widget keys' && git push, then redeploy the apps stack")
print("(ansible-playbook ansible/playbooks/site.yml --limit apps --tags stacks).")
