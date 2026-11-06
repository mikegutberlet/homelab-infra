#!/usr/bin/env python3
"""Copy BIOS/firmware files from the Mac into RomM's bios/<platform>/ folders on the NAS.

Every file under the source folder is hashed (md5) and looked up in RomM's known-firmware list
(ansible/playbooks/files/romm_known_bios.json, copied from rommapp/romm); a match is sent to
/mnt/nfs/Media/Games/bios/<platform>/<expected name> through the media VM as the storage user
(the NAS export squashes root). Files already on the NAS are kept, unknown hashes are listed and
left alone, nothing is deleted anywhere. Goobs runs it on the Mac from the repo root:

    python3 docs/romm_bios_upload.py ~/Emulation/bios            # report only
    python3 docs/romm_bios_upload.py ~/Emulation/bios --apply    # copy

Afterwards: RomM > Scan (firmware is picked up by the scan).
"""
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile

VM = "goobs@192.168.1.51"
DEST = "/mnt/nfs/Media/Games/bios"
KNOWN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ansible", "playbooks", "files",
                     "romm_known_bios.json")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) != 1:
    sys.exit(__doc__)
src, apply = os.path.expanduser(args[0]), "--apply" in sys.argv

known = {}  # md5 -> (platform, file name)
with open(KNOWN) as fh:
    for key, v in json.load(fh).items():
        platform, name = key.split(":", 1)
        known.setdefault(v["md5"], (platform, name))

matched, unknown = {}, []  # "platform/name" -> source path
for root, _, files in os.walk(src):
    for f in sorted(files):
        if f.startswith("."):
            continue
        path = os.path.join(root, f)
        with open(path, "rb") as fh:
            md5 = hashlib.md5(fh.read()).hexdigest()
        if md5 in known:
            platform, name = known[md5]
            matched.setdefault(f"{platform}/{name}", path)
        else:
            unknown.append(os.path.relpath(path, src))

for dest, path in sorted(matched.items()):
    print(f"{'copy' if apply else 'would copy'}  {os.path.relpath(path, src)}  ->  bios/{dest}")
print(f"\n{len(matched)} known firmware files, {len(unknown)} unknown (left alone):")
for u in unknown[:60]:
    print("  ?", u)
if len(unknown) > 60:
    print(f"  ... {len(unknown) - 60} more")

if apply and matched:
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        with tarfile.open(tmp.name, "w") as tar:
            for dest, path in matched.items():
                tar.add(path, arcname=dest)
        tmp_name = tmp.name
    with open(tmp_name, "rb") as fh:
        # -k: never overwrite a file already on the NAS
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", VM,
                            f"sudo -n -u storage mkdir -p {DEST} && sudo -n -u storage tar -x -k -C {DEST}"],
                           stdin=fh, capture_output=True, text=True)
    os.unlink(tmp_name)
    kept = r.stderr.count("Cannot open: File exists") + r.stderr.count("Already exists")
    print(f"\nsent {len(matched)} files to {VM}:{DEST} ({kept} already there, kept)")
    if r.returncode and not kept:
        print(r.stderr.strip()[-800:])
        sys.exit(r.returncode)
    print("Next: RomM > Scan, so the new firmware is picked up.")
