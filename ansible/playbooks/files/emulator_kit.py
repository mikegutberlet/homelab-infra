#!/usr/bin/env python3
"""Fill NAS/Media/Games/emulators/{windows,mac}/ with the newest release of each desktop emulator.

Runs as the storage user on the media VM (playbooks/emulator-kit.yml). For every emulator it asks
the project's GitHub "latest release" API, picks the Windows and Mac asset, and downloads it if
that exact file is not there yet. Older copies are kept (never deleted); versions.txt in the kit
folder says what is current. Without --apply it only prints what it would fetch.
"""
import json
import os
import re
import sys
import urllib.request

KIT = "/mnt/nfs/Media/Games/emulators"
API = "https://api.github.com/repos/{}/releases/latest"
# Dolphin has no GitHub releases; its download page links the files directly.
DOLPHIN = ("https://dolphin-emu.org/download/", r"https://dl\.dolphin-emu\.org/releases/[^\"']+-x64\.7z",
           r"https://dl\.dolphin-emu\.org/releases/[^\"']+-universal\.dmg")
# name: (repo, windows asset regex, mac asset regex). Goobs's Mac is Intel, so x64 or universal.
EMULATORS = {
    "pcsx2": ("PCSX2/pcsx2", r"windows-x64-Qt\.7z$", r"macos-Qt\.tar\.xz$"),
    "rpcs3": ("RPCS3/rpcs3-binaries-win", r"win64.*\.7z$", None),
    "rpcs3-mac": ("RPCS3/rpcs3-binaries-mac", None, r"macos\.7z$"),
    "xemu": ("xemu-project/xemu", r"windows-x86_64\.zip$", r"-macos-universal\.zip$"),
    "ryujinx": ("ryujinx-mirror/ryujinx", r"win_x64\.zip$", r"macos_universal\.app\.tar\.gz$"),
    "cemu": ("cemu-project/Cemu", r"windows-x64\.zip$", r"macos.*x64\.dmg$"),
    "azahar": ("azahar-emu/azahar", r"windows-msvc-[\d.]+\.zip$", r"macos-universal-[\d.]+\.zip$"),
    "openemu": ("OpenEmu/OpenEmu", None, r"^OpenEmu.*\.zip$"),
}


def fetch(url, dest, apply):
    """One download into the kit; returns 1 when something was (or would be) fetched."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  {os.path.basename(dest)} already there")
        return 0
    print(f"  {'fetch' if apply else 'would fetch'} {os.path.basename(dest)}")
    if apply:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        urllib.request.urlretrieve(url, dest + ".part")
        os.replace(dest + ".part", dest)
    return 1


def dolphin(apply, versions):
    try:
        with urllib.request.urlopen(DOLPHIN[0], timeout=30) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"dolphin: download page failed ({e})")
        return 0
    changed = 0
    for folder, rx in (("windows", DOLPHIN[1]), ("mac", DOLPHIN[2])):
        m = re.search(rx, page)
        if not m:
            print(f"dolphin {folder}: no link matches on the download page; get it from dolphin-emu.org")
            continue
        print(f"dolphin {folder}:")
        changed += fetch(m.group(0), os.path.join(KIT, folder, m.group(0).rsplit("/", 1)[1]), apply)
    if m:
        versions.append(f"dolphin\t{m.group(0).split('/releases/')[1].split('/')[0]}\t{DOLPHIN[0]}")
    return changed


def main(apply):
    versions = []
    changed = dolphin(apply, versions)
    for name, (repo, win_rx, mac_rx) in EMULATORS.items():
        try:
            with urllib.request.urlopen(API.format(repo), timeout=30) as r:
                rel = json.load(r)
        except Exception as e:  # noqa: BLE001 - one bad project must not stop the rest
            print(f"{name}: release lookup failed ({e})")
            continue
        tag = rel.get("tag_name", "?")
        versions.append(f"{name}\t{tag}\thttps://github.com/{repo}/releases/tag/{tag}")
        for folder, rx in (("windows", win_rx), ("mac", mac_rx)):
            if not rx:
                continue
            asset = next((a for a in rel.get("assets", []) if re.search(rx, a["name"], re.I)), None)
            if not asset:
                print(f"{name} {folder}: no asset matches /{rx}/ in {tag}; get it from the project site")
                continue
            print(f"{name} {folder} ({asset['size'] // 1048576} MB):")
            changed += fetch(asset["browser_download_url"], os.path.join(KIT, folder, asset["name"]), apply)
    if apply and versions:
        os.makedirs(KIT, exist_ok=True)
        with open(os.path.join(KIT, "versions.txt"), "w") as f:
            f.write("emulator\tversion\trelease page\n" + "\n".join(versions) + "\n")
    print(f"changed: {changed}")


if __name__ == "__main__":
    main("--apply" in sys.argv[1:])
