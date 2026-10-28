"""Create the healthchecks.io checks for the lab's heartbeat and write their ping URLs into
ansible/inventory/hosts.yml. Goobs runs this on the Mac; it asks for the healthchecks.io API key
(Settings > API Access > "Create API key", read-write) and stores nothing but the ping URLs.

    cd ~/HomeLab/homelab-infra && python3 docs/heartbeat_setup.py
    ansible-playbook playbooks/proxmox-heartbeat.yml        # from ansible/, installs the crons

One check per host (period 5 min, grace 10 min, emailed through the account's default channels).
Safe to run again: healthchecks.io returns the existing check of the same name (unique=name), and
a hosts.yml line that already holds a URL is rewritten with the same one.
"""
import getpass
import json
import re
import sys
import urllib.request
from pathlib import Path

API = "https://healthchecks.io/api/v3/checks/"
HOSTS_YML = Path(__file__).resolve().parent.parent / "ansible" / "inventory" / "hosts.yml"
# inventory name -> check name shown in healthchecks.io
CHECKS = {
    "ricksgarage": "RicksGarage",
    "ricksship": "RicksShip",
    "ricksportalgun": "RicksPortalGun",
    "apps": "apps VM (n8n alerting)",
}
PERIOD, GRACE = 300, 600


def create(key, name):
    body = json.dumps({"name": name, "tags": "homelab", "timeout": PERIOD, "grace": GRACE,
                       "channels": "*", "unique": ["name"]}).encode()
    req = urllib.request.Request(API, data=body, method="POST",
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["ping_url"], r.status  # 201 created, 200 already existed


def write_url(text, host, url):
    """Rewrite the heartbeat_url line (commented placeholder or a real one) under this host."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if re.match(rf"^ {{8}}{host}:\s*$", l))
    for i in range(start + 1, len(lines)):
        if re.match(r"^ {8}\S", lines[i]):
            break  # next host
        if "heartbeat_url" in lines[i]:
            lines[i] = f"          heartbeat_url: {url}"
            return "\n".join(lines)
    sys.exit(f"no heartbeat_url line under {host} in {HOSTS_YML}")


def main():
    key = getpass.getpass("healthchecks.io API key (read-write): ").strip()
    if not key:
        sys.exit("No key given.")
    text = HOSTS_YML.read_text()
    for host, name in CHECKS.items():
        try:
            url, status = create(key, name)
        except Exception as e:
            sys.exit(f"Stopped at {name}: {e}")
        print(f"  {'created' if status == 201 else 'exists '}  {name}")
        text = write_url(text, host, url)
    HOSTS_YML.write_text(text)
    print(f"Wrote the ping URLs to {HOSTS_YML}.")
    print("Now: cd ansible && ansible-playbook playbooks/proxmox-heartbeat.yml")
    print("Then commit hosts.yml (the URLs let anyone ping the checks, nothing more; the repo is private).")


if __name__ == "__main__":
    main()
