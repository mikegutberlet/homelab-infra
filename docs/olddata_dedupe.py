#!/usr/bin/env python3
"""Find and delete files in OldData that are exact duplicates of a file kept elsewhere (Goobs, 2026-11-02).

Goobs's rule: everything in OldData is data he keeps unless it is a duplicate of something else.
A file in OldData counts as a duplicate only when its bytes are identical (full SHA-256 after a
size match) to
  - a file under Media, Photos or Documents: the OldData copy is deleted, the other one stays; or
  - another file in OldData: one copy stays (outside .Trash-1000 first, then the shortest path),
    the others are deleted.
to-delete, Backups and sabnzb do not count as "elsewhere" (they are emptied or rotate), and neither
does Media/.tdarr-staging. Files outside OldData are never deleted. Symlinks are skipped; a hardlink
to the kept file is skipped too (deleting it frees nothing). Empty files are ignored.

Runs on TrueNAS as root, from the Mac (repo root):
    ssh root@192.168.1.209 'python3 -' < docs/olddata_dedupe.py              # dry run: report only
    ssh root@192.168.1.209 'python3 - --delete' < docs/olddata_dedupe.py     # delete
The dry run prints every duplicate with the copy that stays and the total. --delete deletes the
same set (re-hashed in that run) and appends each file to OldData/DEDUPED.tsv (time, bytes, deleted
path, kept path, sha256). No snapshots exist on the pool, so the space comes back at once and a
deleted file cannot be restored; the kept copy is byte-identical. Folders are left in place.
"""
import hashlib
import os
import sys
import time
from collections import defaultdict

NAS = "/mnt/DeepData/NAS"
OLD = f"{NAS}/OldData"
REF = [f"{NAS}/Media", f"{NAS}/Photos", f"{NAS}/Documents"]
SKIP = [f"{NAS}/Media/.tdarr-staging"]
LOG = f"{OLD}/DEDUPED.tsv"
DELETE = "--delete" in sys.argv[1:]


def walk(root, sizes=None):
    """(size, path, inode) for regular files under root; with sizes, only files of those sizes."""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(dirpath, d) not in SKIP]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.isfile(p) or os.path.islink(p) or st.st_size == 0:
                continue
            if sizes is None or st.st_size in sizes:
                out.append((st.st_size, p, (st.st_dev, st.st_ino)))
    return out


_hash = {}


def sha(path, quick=False):
    """Full SHA-256, or a quick one of the first and last MiB to rule out most non-matches."""
    key = (path, quick)
    if key not in _hash:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            if quick:
                h.update(fh.read(1 << 20))
                fh.seek(max(0, os.fstat(fh.fileno()).st_size - (1 << 20)))
                h.update(fh.read(1 << 20))
            else:
                for block in iter(lambda: fh.read(8 << 20), b""):
                    h.update(block)
        _hash[key] = h.hexdigest()
    return _hash[key]


def keep_rank(path):  # lower is kept first among OldData copies
    return (".Trash-" in path, len(path), path)


def main():
    t0 = time.time()
    old = walk(OLD)
    sizes = {s for s, _, _ in old}
    print(f"OldData: {len(old)} files, {sum(s for s, _, _ in old) / 1e12:.3f} TB", flush=True)
    ref = [r for root in REF for r in walk(root, sizes)]
    print(f"size matches outside OldData: {len(ref)} files ({time.time() - t0:.0f} s)", flush=True)

    by_size = defaultdict(lambda: {"old": [], "ref": []})
    for s, p, ino in old:
        by_size[s]["old"].append((p, ino))
    for s, p, ino in ref:
        by_size[s]["ref"].append((p, ino))

    plan = []  # (size, delete path, kept path, sha256)
    for size, g in sorted(by_size.items(), reverse=True):
        if len(g["old"]) + len(g["ref"]) < 2:
            continue
        groups = defaultdict(lambda: {"old": [], "ref": []})
        for side in ("old", "ref"):
            for p, ino in g[side]:
                try:
                    groups[(sha(p, True), sha(p))][side].append((p, ino))
                except OSError as e:
                    print(f"skip (unreadable): {p}: {e}", file=sys.stderr, flush=True)
        for (_, full), m in groups.items():
            if not m["old"] or len(m["old"]) + len(m["ref"]) < 2:
                continue
            if m["ref"]:
                kept, kept_ino = m["ref"][0]
                losers = m["old"]
            else:
                ranked = sorted(m["old"], key=lambda x: keep_rank(x[0]))
                (kept, kept_ino), losers = ranked[0], ranked[1:]
            for p, ino in losers:
                if ino == kept_ino:
                    continue  # hardlink to the kept file: deleting it frees nothing
                plan.append((size, p, kept, full))

    total = sum(s for s, _, _, _ in plan)
    for size, p, kept, _ in plan:
        print(f"{'DELETE' if DELETE else 'dup'}\t{size / 1e9:8.2f} GB\t{p}\n\t\tkept: {kept}")
    print(f"\n{len(plan)} duplicate files in OldData, {total / 1e9:.1f} GB "
          f"({'deleting' if DELETE else 'dry run, nothing deleted; add --delete to delete'}); {time.time() - t0:.0f} s")
    if not DELETE:
        return
    with open(LOG, "a") as log:
        for size, p, kept, full in plan:
            if not (p.startswith(OLD + "/") and os.path.isfile(kept) and os.path.getsize(p) == size):
                print(f"not deleted (changed since the check): {p}", file=sys.stderr)
                continue
            os.remove(p)
            log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{size}\t{p}\t{kept}\t{full}\n")
    print(f"done; list in {LOG}")


if __name__ == "__main__":
    main()
