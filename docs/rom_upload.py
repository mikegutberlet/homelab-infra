#!/usr/bin/env python3
"""Sort ROMs lying in a Mac folder into RomM's console folders on the NAS.

Each file is assigned a console from its extension; a .zip is judged by what is inside it (and
stays zipped, RomM reads zips); a disc image (.iso/.bin+.cue) is opened to tell PS1, PS2 and PSP
apart (PSP_GAME folder, SYSTEM.CNF BOOT2= vs BOOT=). Matches are streamed over ssh to the media VM
and unpacked as the storage user into /mnt/nfs/Media/Games/ROMs/<console>/ (existing files are
kept); the local copies then move to <source>/roms-moved/ so nothing is lost. Files it cannot
place (.7z, .chd, unknown extensions) are listed and left alone. Goobs runs it on the Mac:

    python3 docs/rom_upload.py ~/Downloads            # report only
    python3 docs/rom_upload.py ~/Downloads --apply    # copy + move the local files aside
    python3 docs/rom_upload.py ~/Downloads --apply --put win=Reborn.zip --put mac=Rejuv-macos.zip

--put places a file by hand; PC fan games (RPG Maker, Java) go to win / mac, download-only in RomM.

Consoles get Goobs's existing folder names (GB, GBColor, GBA, NDS, N64, PS1, PS2, PSP); consoles
not on the NAS yet get RomM's slug (nes, snes, genesis, ...), which RomM recognises without any
config change. Afterwards: RomM > Scan.
"""
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile

VM = "goobs@192.168.1.51"
DEST = "/mnt/nfs/Media/Games/ROMs"
BY_EXT = {
    ".gb": "GB", ".gbc": "GBColor", ".gba": "GBA", ".nds": "NDS", ".dsi": "NDS",
    ".n64": "N64", ".z64": "N64", ".v64": "N64", ".cso": "PSP", ".pbp": "PSP",
    ".nes": "nes", ".fds": "fds", ".sfc": "snes", ".smc": "snes",
    ".md": "genesis", ".gen": "genesis", ".smd": "genesis", ".32x": "sega32",
    ".gg": "gamegear", ".sms": "sms", ".pce": "tg16", ".ngp": "ngp", ".ngc": "ngpc",
    ".vb": "virtualboy", ".ws": "wonderswan", ".wsc": "wonderswan-color", ".lnx": "lynx", ".a26": "atari2600",
    ".gcm": "ngc", ".wbfs": "wii", ".nsp": "switch", ".xci": "switch", ".3ds": "3ds", ".cia": "3ds",
}
DISC = {".iso", ".bin", ".img", ".mdf"}

args, forced, argv = [], {}, sys.argv[1:]
while argv:
    a = argv.pop(0)
    if a == "--put":  # --put win=Reborn.zip : place a file by hand (PC fan games, odd extensions)
        console, _, name = argv.pop(0).partition("=")
        forced[name] = console
    elif not a.startswith("--"):
        args.append(a)
if len(args) != 1:
    sys.exit(__doc__)
src, apply = os.path.expanduser(args[0]), "--apply" in sys.argv
MOVED = os.path.join(src, "roms-moved")


def iso_console(path):
    """PS1 / PS2 / PSP from an ISO9660 image's root directory; None when unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(16 * 2048)
            pvd = fh.read(2048)
            if pvd[1:6] != b"CD001":
                return None
            root = pvd[156:190]
            lba, size = struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0]
            fh.seek(lba * 2048)
            data, pos, entries = fh.read(size), 0, {}
            while pos < len(data):
                ln = data[pos]
                if ln == 0:
                    pos = (pos // 2048 + 1) * 2048  # next sector
                    continue
                name = data[pos + 33:pos + 33 + data[pos + 32]].split(b";")[0].decode("ascii", "ignore")
                entries[name.upper()] = struct.unpack_from("<I", data, pos + 2)[0], struct.unpack_from("<I", data, pos + 10)[0]
                pos += ln
            if "PSP_GAME" in entries:
                return "PSP"
            if "SYSTEM.CNF" in entries:
                lba, size = entries["SYSTEM.CNF"]
                fh.seek(lba * 2048)
                cnf = fh.read(min(size, 4096)).upper()
                return "PS2" if b"BOOT2" in cnf else "PS1" if b"BOOT" in cnf else None
    except (OSError, struct.error):
        pass
    return None


MD_MIN = 128 * 1024  # a .md smaller than this is a README, not a Mega Drive ROM


def ext_console(name, size):
    ext = os.path.splitext(name)[1].lower()
    if ext == ".md" and size < MD_MIN:
        return None
    return BY_EXT.get(ext)


def console_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in BY_EXT:
        return ext_console(path, os.path.getsize(path))
    if ext in DISC:
        return iso_console(path)
    if ext == ".zip":
        try:
            inner = {ext_console(i.filename, i.file_size) for i in zipfile.ZipFile(path).infolist()}
        except zipfile.BadZipFile:
            return None
        inner.discard(None)
        return inner.pop() if len(inner) == 1 else None
    return None


plan, skipped = {}, []  # "console/filename" -> local path (a .cue goes along with its .bin)
for f in sorted(os.listdir(src)):
    path = os.path.join(src, f)
    if f.startswith(".") or not os.path.isfile(path):
        continue
    ext = os.path.splitext(f)[1].lower()
    if ext == ".cue":
        continue  # handled with its .bin
    console = forced.get(f) or console_of(path)
    if console is None:
        if ext == ".md" and os.path.getsize(path) < MD_MIN:
            continue  # a Markdown file
        if ext in BY_EXT or ext in DISC or ext in (".zip", ".7z", ".chd", ".rar"):
            skipped.append(f)
        continue
    plan[f"{console}/{f}"] = path
    cue = os.path.splitext(path)[0] + ".cue"
    if ext == ".bin" and os.path.exists(cue):
        plan[f"{console}/{os.path.basename(cue)}"] = cue

for dest, path in sorted(plan.items()):
    print(f"{'copy' if apply else 'would copy'}  {os.path.basename(path)}  ->  ROMs/{dest}")
print(f"\n{len(plan)} files placed, {len(skipped)} ROM-like files I cannot place (tell me the console):")
for s in skipped:
    print("  ?", s)

if apply and plan:
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
        with tarfile.open(tmp.name, "w") as tar:
            for dest, path in plan.items():
                tar.add(path, arcname=dest)
        tmp_name = tmp.name
    with open(tmp_name, "rb") as fh:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", VM, f"sudo -n -u storage tar -x -k -C {DEST}"],
                           stdin=fh, capture_output=True, text=True)
    os.unlink(tmp_name)
    kept = r.stderr.count("File exists") + r.stderr.count("Already exists")
    if r.returncode and not kept:
        print(r.stderr.strip()[-800:])
        sys.exit(r.returncode)
    os.makedirs(MOVED, exist_ok=True)
    for path in plan.values():
        shutil.move(path, os.path.join(MOVED, os.path.basename(path)))
    print(f"\nsent {len(plan)} files to the NAS ({kept} were already there, kept); local copies are in {MOVED}")
    print("Next: RomM > Scan.")
