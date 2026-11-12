#!/usr/bin/env python3
"""Two Listenarr leftovers from the 2026-11-05 audiobook pass (playbooks/audiobook-fixups.yml).

Runs as root on the media VM; `--dry-run` only reports. Nothing is deleted.

1. The Hardcover titles the Want to Read sync never matched (its title+author match is strict)
   are looked up through Listenarr's Audible search, the first human-read result whose title
   contains the wanted title and whose authors include the author's surname is added monitored
   with a search, unless its ASIN is already in the library. The dry run prints the pick.
2. Bad Blood (John Carreyrou) is imported by hand from the SABnzbd folder its import got blocked
   on (a "(ungekürzt)" release that landed three times): the library row is found by title, the
   folder by glob (override with --source), Listenarr's manual-import preview lists the files and
   the import runs with the hardlink/copy action the UI uses, so the source folder stays as it is.

The Listenarr API key is read from the media stack's decrypted .env and never printed.
"""
import glob
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DRY = "--dry-run" in sys.argv
LISTENARR = "http://127.0.0.1:4545/api/v1/"
ENV = "/opt/stacks/media/.env"
SOURCE_GLOB = "/mnt/nfs/sabnzb/complete/audiobooks/John Carreyrou - Bad Blood*.1"
ADD = [  # (title, author) as Hardcover has them
    ("The Hitchhiker's Guide to the Galaxy", "Douglas Adams"),
    ("Twenty Thousand Leagues Under the Sea", "Jules Verne"),
    ("The Martian Chronicles", "Ray Bradbury"),
    ("The Moon Is a Harsh Mistress", "Robert A. Heinlein"),
]
IMPORT = ("Bad Blood", "Carreyrou")

args = [a for a in sys.argv[1:] if a != "--dry-run"]
if "--source" in args:
    SOURCE_GLOB = args[args.index("--source") + 1]

KEY = re.search(r"^LISTENARR_API_KEY=['\"]?([^'\"\n]+)", open(ENV).read(), re.M).group(1)
changes, todo = [], []


def api(path, method="GET", body=None, ok404=False):
    req = urllib.request.Request(LISTENARR + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Api-Key": KEY, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return None
        sys.exit(f"Listenarr {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    return json.loads(data) if data else None


def norm(s):
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"\b(the|a|an)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def note(what):
    changes.append(what)
    print(("would: " if DRY else "did: ") + what, flush=True)


def ask(what):
    todo.append(what)
    print("for Goobs: " + what, flush=True)


# 1. the four Hardcover titles
for title, author in ADD:
    found = (api(f"search/audible?query={urllib.parse.quote(title + ' ' + author)}&region=us") or {}).get("results") or []
    surname = norm(author.split()[-1])
    hit = next((r for r in found
                if norm(title) in norm(r.get("title"))
                and any(surname in norm(a.get("name")) for a in r.get("authors") or [])
                and not any("virtual voice" in (n.get("name") or "").lower() for n in r.get("narrators") or [])), None)
    if hit is None:
        ask(f"\"{title}\" by {author}: no Audible result fits ({len(found)} results); add it by hand")
        continue
    asin = hit.get("asin")
    if api(f"library/by-asin/{urllib.parse.quote(asin or '-')}", ok404=True) is not None:
        print(f"present: \"{hit.get('title')}\" (ASIN {asin})", flush=True)
        continue
    narr = ", ".join(n.get("name") or "" for n in (hit.get("narrators") or [])[:2])
    note(f"add \"{hit.get('title')}\" read by {narr or '?'} (ASIN {asin}), monitored, search queued")
    if DRY:
        continue
    series = (hit.get("series") or [{}])[0]
    meta = {
        "asin": asin, "source": "Audible", "region": "us",
        "title": hit.get("title"), "subtitle": hit.get("subtitle"),
        "authors": [a["name"] for a in hit.get("authors") or [] if a.get("name")],
        "narrators": [n["name"] for n in hit.get("narrators") or [] if n.get("name")],
        "imageUrl": hit.get("imageUrl"), "publisher": hit.get("publisher"), "language": hit.get("language"),
        "runtime": hit.get("runtimeLengthMin") or hit.get("lengthMinutes") or hit.get("runtimeMinutes"),
        "publishYear": (hit.get("releaseDate") or "")[:4] or None, "publishedDate": (hit.get("releaseDate") or "")[:10] or None,
        "series": series.get("name"), "seriesNumber": series.get("position"),
        "genres": [g["name"] for g in hit.get("genres") or [] if g.get("name")], "isbn": [],
    }
    api("library/add", "POST", {"metadata": meta, "monitored": True, "autoSearch": True})

# 2. Bad Blood by hand
title, surname = IMPORT
rows = [a for a in api("library") or []
        if norm(a.get("title")).startswith(norm(title)) and any(norm(surname) in norm(x) for x in a.get("authors") or [])]
dirs = sorted(glob.glob(SOURCE_GLOB))
if len(rows) != 1:
    ask(f"{title}: {len(rows)} library rows match; import it from the UI")
elif rows[0].get("fileCount") or rows[0].get("filePath"):
    print(f"present: \"{rows[0].get('title')}\" already has files ({rows[0].get('filePath')})", flush=True)
elif len(dirs) != 1:
    ask(f"{title}: {len(dirs)} folders match {SOURCE_GLOB!r}; pass --source <folder>")
else:
    book, src = rows[0], dirs[0]
    items = (api(f"library/manual-import/preview?path={urllib.parse.quote(src)}") or {}).get("items") or []
    audio = [i for i in items if i["relativePath"].lower().rsplit(".", 1)[-1] in ("m4b", "m4a", "mp3", "flac", "ogg", "opus")]
    if not audio:
        ask(f"{title}: no audio files in {src}")
    else:
        note(f"import {len(audio)} file(s) from {src} into \"{book['title']}\" (id {book['id']}, hardlink/copy)")
        if not DRY:
            res = api("library/manual-import", "POST", {
                "path": src, "mode": "interactive", "action": "hardlink/copy",
                "includeCompanionFiles": True, "cleanupEmptySourceFolders": False,
                "items": [{"relativePath": i["relativePath"], "fullPath": i["fullPath"], "matchedAudiobookId": book["id"]}
                          for i in audio]}) or {}
            print(f"imported {res.get('importedCount')}/{res.get('totalCount')}", flush=True)
            for r in res.get("results") or []:
                if not r.get("success"):
                    ask(f"{r.get('sourcePath')}: {r.get('error') or r.get('skipReason')}")

print(f"\n{'would change' if DRY else 'changed'}: {len(changes)}; for Goobs: {len(todo)}")
