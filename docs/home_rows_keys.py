#!/usr/bin/env python3
"""Put the curated-rows credentials (Kometa + home-rows) into stacks/gpu/secrets.sops.env.

Run on the Mac (needs the lab SSH key and the age key in ~/.config/sops/age/keys.txt):

    python3 docs/home_rows_keys.py

- PLEX_TOKEN: read from Plex's Preferences.xml on the gpu VM.
- HOME_ROWS_EMBY_KEY: an Emby API key named "home-rows", created with the Homepage widget's Emby
  key (stacks/apps) when it does not exist yet. Undo: Emby > Settings > API Keys.
- TMDB_API_KEY / MDBLIST_API_KEY: from ~/.config/homelab/tmdb.env and mdblist.env, which Goobs
  saved by hand; the files are deleted once the values are in the encrypted file.

Values are never printed. Re-running is safe: every key is replaced, not duplicated.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

GPU, APPS = "goobs@192.168.1.52", "goobs@192.168.1.53"
EMBY = "http://192.168.1.52:8096/emby"
HOME = os.path.expanduser("~")
HAND_FILES = [f"{HOME}/.config/homelab/tmdb.env", f"{HOME}/.config/homelab/mdblist.env"]
os.environ["SOPS_AGE_KEY_FILE"] = f"{HOME}/.config/sops/age/keys.txt"
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SOPS = next((p for p in [shutil.which("sops"), "/usr/local/bin/sops", "/opt/homebrew/bin/sops",
                         f"{HOME}/.local/bin/sops", f"{HOME}/go/bin/sops"] if p and os.path.exists(p)), None)


def sops(args, stdin):
    """sops on stdin: locally when installed, else on the apps VM (same as homepage_keys.py)."""
    if SOPS:
        cmd = [SOPS, *args, "/dev/stdin"]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", APPS,
               "sudo SOPS_AGE_KEY_FILE=/root/.config/sops/age/keys.txt sops " + " ".join(args) + " /dev/stdin"]
    # cwd "/" keeps sops from finding the repo's .sops.yaml (its rules match real paths, not stdin).
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd="/")
    if r.returncode:
        sys.exit(f"sops failed ({'local' if SOPS else 'on the apps VM'}): {r.stderr.strip()[-300:]}")
    return r.stdout


def decrypt(path):
    plain = sops(["--decrypt", "--input-type", "dotenv", "--output-type", "dotenv"], open(path).read())
    return dict(ln.partition("=")[::2] for ln in plain.splitlines() if "=" in ln)


def update_secrets(path, vals):
    enc = open(path).read()
    recips = ",".join(re.findall(r"^sops_age__list_\d+__map_recipient=(.*)$", enc, re.M))
    plain = sops(["--decrypt", "--input-type", "dotenv", "--output-type", "dotenv"], enc)
    lines = [ln for ln in plain.splitlines() if ln.partition("=")[0] not in vals]
    lines += [f"{k}={v}" for k, v in vals.items()]
    out = sops(["--encrypt", "--age", recips, "--input-type", "dotenv", "--output-type", "dotenv"], "\n".join(lines) + "\n")
    open(path, "w").write(out)
    print(f"Updated {path} ({len(vals)} keys)")


def emby(path, key, method="GET"):
    req = urllib.request.Request(f"{EMBY}{path}", method=method, headers={"X-Emby-Token": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return json.loads(data) if data.strip() else None


vals = {}


def need(name, value):
    if value:
        vals[name] = value
        print(f"  {name}: ok")
    else:
        print(f"  {name}: NOT FOUND (left as is)")


r = subprocess.run(["ssh", "-o", "BatchMode=yes", GPU,
                    "sudo grep -o 'PlexOnlineToken=\"[^\"]*\"' '/opt/appdata/plex/Library/Application Support/Plex Media Server/Preferences.xml'"],
                   capture_output=True, text=True)
need("PLEX_TOKEN", r.stdout.strip().split('"')[1] if '"' in r.stdout else "")

admin = decrypt("stacks/apps/secrets.sops.env").get("HOMEPAGE_EMBY_KEY", "")
token = ""
if admin:
    def find():
        return next((k["AccessToken"] for k in emby("/Auth/Keys", admin)["Items"] if k.get("AppName") == "home-rows"), "")
    token = find()
    if not token:
        emby("/Auth/Keys?App=home-rows", admin, "POST")
        token = find()
need("HOME_ROWS_EMBY_KEY", token)

for path in HAND_FILES:
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k in ("TMDB_API_KEY", "MDBLIST_API_KEY") and v:
                need(k, v)
for k in ("TMDB_API_KEY", "MDBLIST_API_KEY"):
    if k not in vals:
        print(f"  {k}: not in ~/.config/homelab (left as is)")

if vals:
    update_secrets("stacks/gpu/secrets.sops.env", vals)
for p in HAND_FILES:
    if os.path.exists(p):
        os.remove(p)
print("Done. Commit and push, then deploy the gpu stack:")
print("  ansible-playbook ansible/playbooks/site.yml --limit gpu --tags stacks")
