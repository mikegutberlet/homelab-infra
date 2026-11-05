#!/usr/bin/env python3
"""Tidy the ROM library for RomM (playbooks/rom-organize.yml). Audit of 2026-11-05 in the thread.

Runs on the media VM as the storage user (the NAS export squashes root). Without --apply it only
prints what it would do. Nothing is ever deleted: every file that leaves the library goes to the
holding folder /mnt/nfs/to-delete/<date>-roms/ with a manifest.tsv (src<TAB>dst) for undo, and
--cleanup only removes folders that are empty.

  default        GBColor/*.gb -> GB; strip the "0223 " release numbers from NDS names so RomM
                 matches titles; archives that sit next to their extracted game folder, the N64
                 pack archived in PS2, the two .7z.part downloads, .DS_Store -> holding
  --extract      unpack "165 GBA Rompack.7z" and "34 Pokemon Games (NDS GBA GBC GB).7z", sort by
                 extension into GB/GBColor/GBA/NDS (a name that already exists -> holding), then
                 the pack archive -> holding; needs 7z on the VM
  --bios         unpack "BIOS files/*" into Games/bios/<romm slug>/ (RomM's firmware folder)
  --bios-names   rename firmware files to the names the emulators look for (md5 against RomM's
                 known_bios_files.json, shipped next to this script); unknown hashes are left
  --nds-english  one region per NDS title: keep US copies, else EU/AU/UK, move the other
                 regions of that title (JP, FR, DE, IT, ES, KS...) -> holding
  --cleanup      rmdir the empty leftovers: "God of War II (...)", "Paste To Phone", the stray
                 lowercase Games/roms tree RomM created on 2026-10-21 (all empty folders)
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

GAMES = os.environ.get("ROM_GAMES", "/mnt/nfs/Media/Games")  # override for a local test
ROMS = f"{GAMES}/ROMs"
BIOS = f"{GAMES}/bios"
HOLD = os.environ.get("ROM_HOLD", f"/mnt/nfs/to-delete/{datetime.date.today():%Y-%m-%d}-roms")
ARCH = (".zip", ".7z", ".rar")
BY_EXT = {".gb": "GB", ".gbc": "GBColor", ".gba": "GBA", ".nds": "NDS"}
# BIOS archive -> RomM slug (one archive may serve several platforms, sorted by file name)
BIOS_MAP = {
    "Gameboy Bios (GB-GBC-GBA).7z": {"gb": r"(gb_bios|dmg)", "gbc": r"(gbc|cgb)", "gba": r"gba"},
    "Nintendo DS Bios and Firmware.rar": {"nds": r"."},
    "PSX PSOne Bios.7z": {"psx": r"."},
    "PS2 BIOS Complete (All Regions).7z": {"ps2": r"."},
    "Nintendo Gamecube (GCN) BIOS.7z": {"ngc": r"."},
    "Dreamcast Bios Pack.7z": {"dc": r"."},
    "Microsoft XBox Bios.zip": {"xbox": r"."},
    "Atari Bios Collection.7z": {"atari-bios": r"."},  # mixed consoles; sort by hand later
    "nes to snes adapter bios.zip": {"snes": r"."},
}
NDS_NUM = re.compile(r"^\d{4} ")
TAG = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
REGION = re.compile(r"\((US|EU|JP|FR|DE|IT|ES|KS|AU|UK|NL|CN|SE|DK|NO|FI|AS|CA)\)")
ENGLISH, PREFERRED = {"US", "EU", "AU", "UK"}, "US"

ap = argparse.ArgumentParser()
for f in ("apply", "extract", "bios", "bios-names", "nds-english", "cleanup"):
    ap.add_argument("--" + f, action="store_true")
A = ap.parse_args()
n_changes = 0
planned = set()  # names claimed earlier in this run (in report mode nothing is renamed yet)
SEVENZ = shutil.which("7zz") or shutil.which("7z")  # 7zz (Debian "7zip") also unpacks .rar


def act(what):
    global n_changes
    n_changes += 1
    print(("did: " if A.apply else "would: ") + what, flush=True)


def hold(src):
    """Move a file or folder into the holding folder, keeping its path below Games/."""
    dst = os.path.join(HOLD, os.path.relpath(src, GAMES))
    act(f"hold {os.path.relpath(src, GAMES)}")
    if A.apply:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        with open(f"{HOLD}/manifest.tsv", "a") as m:
            m.write(f"{src}\t{dst}\n")


def move(src, dst):
    if os.path.exists(dst):
        hold(src)  # a copy with that name is already there
        return
    act(f"move {os.path.relpath(src, GAMES)} -> {os.path.relpath(dst, GAMES)}")
    if A.apply:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)


def rename(path, new_name):
    dst = os.path.join(os.path.dirname(path), new_name)
    if os.path.exists(dst) or dst in planned:
        print(f"skip (name taken, same dump twice): {os.path.relpath(path, GAMES)} -> {new_name}")
        return
    planned.add(dst)
    act(f"rename {os.path.relpath(path, GAMES)} -> {new_name}")
    if A.apply:
        os.rename(path, dst)


def unpack(archive, into):
    act(f"unpack {os.path.relpath(archive, GAMES)} -> {os.path.relpath(into, GAMES)}")
    if A.apply:
        os.makedirs(into, exist_ok=True)
        r = subprocess.run([SEVENZ, "x", "-y", f"-o{into}", archive], capture_output=True, text=True)
        if r.returncode and archive.lower().endswith(".rar") and shutil.which("unar"):
            r = subprocess.run(["unar", "-q", "-f", "-o", into, archive], capture_output=True, text=True)
        if r.returncode:
            print(f"  could not unpack (left in place): {r.stderr.strip()[-200:]}")
            return False
    else:
        r = subprocess.run([SEVENZ, "l", "-ba", archive], capture_output=True, text=True)
        files = [l.split(None, 5)[-1] for l in r.stdout.splitlines() if l.strip()]
        print(f"  contents ({len(files)}): " + ", ".join(files[:12]) + (" ..." if len(files) > 12 else ""))
    return True


def ls(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def sort_loose(staging, platform=None):
    """Files unpacked from a rompack: by extension into the console folders. A zip per game is the
    normal No-Intro form, so in a single-console pack nested archives go to that console."""
    for root, _, files in os.walk(staging):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in BY_EXT:
                move(os.path.join(root, f), os.path.join(ROMS, BY_EXT[ext], f))
            elif ext in ARCH or ext == ".gz":
                if platform:
                    move(os.path.join(root, f), os.path.join(ROMS, platform, f))
                else:
                    print(f"  left in staging (nested archive, look by hand): {f}")
            else:
                hold(os.path.join(root, f))


# ---- default pass -------------------------------------------------------------------------------
print(f"library {ROMS}  holding {HOLD}  mode {'APPLY' if A.apply else 'report only'}")
if not os.path.isdir(BIOS):
    act("mkdir bios (RomM's firmware folder; compose binds it, and Docker could not create it as root)")
    if A.apply:
        os.makedirs(BIOS)
for f in ls(f"{ROMS}/GBColor"):
    if f.lower().endswith(".gb"):
        move(f"{ROMS}/GBColor/{f}", f"{ROMS}/GB/{f}")
for f in ls(f"{ROMS}/NDS"):
    if NDS_NUM.match(f):
        rename(f"{ROMS}/NDS/{f}", NDS_NUM.sub("", f))
for plat in ("PS2", "PSP", "GBA", "N64"):
    for f in ls(f"{ROMS}/{plat}"):
        base, ext = os.path.splitext(f)
        if ext.lower() in ARCH and os.path.isdir(f"{ROMS}/{plat}/{base}"):
            hold(f"{ROMS}/{plat}/{f}")  # archive next to its own extracted folder
for f in ls(f"{ROMS}/PSP"):  # PS2 mod archives filed under PSP; the unpacked ISOs sit in PS2/
    if f.startswith("GTASA - A True Definitive") and f.endswith(".7z"):
        hold(f"{ROMS}/PSP/{f}")
if os.path.exists(f"{ROMS}/PS2/GoldenEye Compilation Pack (v1.1).7z"):
    hold(f"{ROMS}/PS2/GoldenEye Compilation Pack (v1.1).7z")  # N64 pack, already unpacked in N64/
for f in ls(GAMES):
    if f.endswith(".7z.part") or f == ".DS_Store":
        hold(f"{GAMES}/{f}")

# ---- rompacks -----------------------------------------------------------------------------------
if A.extract:
    if not SEVENZ:
        sys.exit("7z not installed on the VM (apt install p7zip-full 7zip)")
    for pack, platform in ((f"{ROMS}/GBA/165 GBA Rompack.7z", "GBA"),
                           (f"{ROMS}/34 Pokemon Games (NDS GBA GBC GB).7z", None)):
        staging = f"{ROMS}/_unpacked/{os.path.basename(pack)[:-3]}"
        if os.path.exists(pack):
            if not unpack(pack, staging):
                continue
            hold(pack)
        if A.apply and os.path.isdir(staging):  # also finishes a staging folder left by an earlier run
            sort_loose(staging, platform)
            for root, dirs, files in os.walk(staging, topdown=False):
                if not dirs and not files:
                    os.rmdir(root)

# ---- firmware -----------------------------------------------------------------------------------
if A.bios:
    if not SEVENZ:
        sys.exit("7z not installed on the VM (apt install p7zip-full 7zip)")
    for archive, rules in BIOS_MAP.items():
        src = f"{ROMS}/BIOS files/{archive}"
        if not os.path.exists(src):
            continue
        staging = f"{BIOS}/_unpacked/{archive}"
        if os.path.isdir(staging) and any(os.scandir(staging)):
            continue  # done by an earlier run
        if not unpack(src, staging):
            continue
        if A.apply:
            for root, _, files in os.walk(staging):
                for f in files:
                    slug = next((s for s, rx in rules.items() if re.search(rx, f, re.I)), None)
                    if f.lower().endswith((".url", ".txt", ".nfo")):
                        hold(os.path.join(root, f))
                    elif slug and not f.lower().endswith(ARCH):
                        move(os.path.join(root, f), f"{BIOS}/{slug}/{f}")
                    else:
                        print(f"  left in bios/_unpacked (sort by hand): {archive}/{f}")

# ---- firmware file names --------------------------------------------------------------------------
if A.bios_names:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "romm_known_bios.json")) as fh:
        known = {}
        for key, v in json.load(fh).items():
            slug, name = key.split(":", 1)
            known.setdefault(slug, {})[v["md5"]] = name
    for slug in ls(BIOS):
        if slug.startswith("_") or slug not in known:
            continue
        for f in ls(f"{BIOS}/{slug}"):
            path = f"{BIOS}/{slug}/{f}"
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                md5 = hashlib.md5(fh.read()).hexdigest()
            name = known[slug].get(md5)
            if name is None:
                print(f"  not a known {slug} firmware hash, left alone: {f}")
            elif name != f:
                rename(path, name)

# ---- one region per NDS title -------------------------------------------------------------------
if A.nds_english:
    titles = {}
    for f in ls(f"{ROMS}/NDS"):
        if not f.lower().endswith(ARCH + (".nds",)):
            continue
        name = NDS_NUM.sub("", os.path.splitext(f)[0])
        regions = set(REGION.findall(name))
        titles.setdefault(TAG.sub("", name).strip().lower(), []).append((f, regions))
    for copies in titles.values():
        have = set().union(*(r for _, r in copies))
        keep = {PREFERRED} if PREFERRED in have else (have & ENGLISH)
        if not keep:
            continue  # no English copy: keep whatever there is
        for f, regions in copies:
            if regions and not (regions & keep):
                hold(f"{ROMS}/NDS/{f}")

# ---- empty leftovers ----------------------------------------------------------------------------
if A.cleanup:
    for d in [f"{ROMS}/God of War II (Europe, Australia) (En,Fr,De,Es,It,Ru)", f"{GAMES}/Paste To Phone",
              f"{GAMES}/roms"]:
        if not os.path.isdir(d):
            continue
        if any(os.path.isfile(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs):
            print(f"not empty, left alone: {d}")
            continue
        act(f"rmdir (empty) {os.path.relpath(d, GAMES)}")
        if A.apply:
            try:
                shutil.rmtree(d)
            except PermissionError as e:
                print(f"  could not remove {d}: {e} (owned by nobody; needs the TrueNAS shell)")

print(f"{'changed' if A.apply else 'would change'}: {n_changes}")
