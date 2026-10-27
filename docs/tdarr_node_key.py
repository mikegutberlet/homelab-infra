#!/usr/bin/env python3
"""Put the Tdarr API key into stacks/transcode/secrets.sops.env, encrypted.

Goobs runs this on the Mac from the repo root (needs the lab SSH key):
    python3 docs/tdarr_node_key.py          # use the key already in /root/tdarr-api-key on the gpu VM
    python3 docs/tdarr_node_key.py --new    # first store a new key there (asked for, never echoed);
                                            # also updates gpu-vm's built-in node and restarts Tdarr.
                                            # Delete the old key in Tdarr only after this ran.

The transcode VM's Tdarr node needs the server's API key (the server has auth on; without it the
node gets 401). The key is read on the gpu VM and encrypted with sops, locally when sops is
installed, else on the gpu VM, which has sops for the Ansible deploys. The plaintext never touches
the repo, a disk or the screen. 2026-10-27: a first try without sops on the Mac committed it in
plaintext, so the file is checked for sops metadata before it is written.
"""
import getpass, os, re, shutil, subprocess, sys

HOME = os.path.expanduser("~")
GPU = "goobs@192.168.1.52"
OUT = "stacks/transcode/secrets.sops.env"

if not os.path.exists(".sops.yaml"):
    sys.exit("Run this from the repo root (~/HomeLab/homelab-infra).")
RECIPIENT = re.search(r"age:\s*(age1\w+)", open(".sops.yaml").read()).group(1)


def ssh(cmd, stdin=None):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", GPU, cmd], input=stdin, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ssh {GPU} failed: {r.stderr.strip()[-300:]}")
    return r.stdout


if "--new" in sys.argv:
    new = getpass.getpass("New Tdarr API key (not shown): ").strip()
    if not new:
        sys.exit("No key given; nothing changed.")
    ssh("sudo install -m 600 -o root -g root /dev/stdin /root/tdarr-api-key", new + "\n")
    print("Stored the new key in /root/tdarr-api-key on the gpu VM.")
    # The gpu VM's built-in node logs in with its own copy of the key; after the old key was deleted
    # on 2026-10-27 it dropped out of Tdarr until this file was updated and Tdarr restarted.
    ssh("F=/opt/appdata/tdarr/configs/Tdarr_Node_Config.json && sudo python3 -c "
        "'import json,sys; p=sys.argv[1]; c=json.load(open(p)); c[\"apiKey\"]=open(\"/root/tdarr-api-key\").read().strip(); "
        "json.dump(c, open(p, \"w\"), indent=2)' $F && sudo docker restart gpu-tdarr-1 >/dev/null")
    print("Updated gpu-vm's own node config and restarted Tdarr on the gpu VM.")

key = ssh("sudo cat /root/tdarr-api-key").strip()
if not key:
    sys.exit("/root/tdarr-api-key on the gpu VM is empty.")
plain = f"TDARR_API_KEY={key}\n"

SOPS = next((p for p in [shutil.which("sops"), "/usr/local/bin/sops", "/opt/homebrew/bin/sops",
                         f"{HOME}/.local/bin/sops", f"{HOME}/go/bin/sops"] if p and os.path.exists(p)), None)
args = ["--encrypt", "--age", RECIPIENT, "--input-type", "dotenv", "--output-type", "dotenv", "/dev/stdin"]
if SOPS:
    # cwd "/" keeps sops from finding .sops.yaml, whose rules match only the real file path.
    r = subprocess.run([SOPS, *args], input=plain, capture_output=True, text=True, cwd="/")
    if r.returncode:
        sys.exit(f"sops failed locally: {r.stderr.strip()[-300:]}")
    enc = r.stdout
else:
    enc = ssh("cd / && sudo /usr/local/bin/sops " + " ".join(args), plain)

if key in enc or "sops_mac=" not in enc:
    sys.exit("The output does not look encrypted; nothing written.")
with open(OUT, "w") as f:
    f.write(enc)
print(f"Wrote {OUT} (encrypted, {'local sops' if SOPS else 'sops on the gpu VM'}).")
