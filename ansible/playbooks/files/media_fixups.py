#!/usr/bin/env python3
"""One-off repairs found by the 2026-10-28 download-path audit (playbooks/media-fixups.yml).

Runs as root on the media VM; `--dry-run` only reports. Every step reads first and writes only
when something differs, so a second run changes nothing. Nothing is deleted from disk.

  Radarr/Sonarr  minimum age 15 min for Usenet grabs: fresh posts failed as "cannot be
                 completed" before all articles had propagated (Terminator 3, Matchbox, 10-27)
  Radarr         14 movies still point at the old Servarr VM's SMB path
                 (/mnt/backupsmb/DeepData/Media/Movies): repoint them (and the collection that
                 used it) at /mnt/nfs/Media/Movies, no files moved; clears the two health errors
                 and lets the backlog search find them
  Radarr         queue items stuck on "manual import required": import the one video file of the
                 download when Radarr itself knows the movie (movieId from its own grab record)
                 and has no objection to the file; anything else is listed for Goobs
  Sonarr         the same series added eight times under "Faraway Downs" names, all TVDB-deleted:
                 remove the copies without files from Sonarr (library files untouched); the other
                 TVDB-deleted series are listed for Goobs

The API keys are read from the apps' config files and never printed.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DRY = "--dry-run" in sys.argv
APPDATA = "/opt/appdata"
OLD_ROOT, NEW_ROOT = "/mnt/backupsmb/DeepData/Media/Movies", "/mnt/nfs/Media/Movies"
DOWNLOADS = "/mnt/nfs/sabnzb/"
MIN_AGE = 15
VIDEO = re.compile(r"\.(mkv|mp4|avi|m4v|ts|wmv|mov)$", re.I)
changes, todo = [], []


def note(what):
    changes.append(what)
    print(("would: " if DRY else "did: ") + what, flush=True)


def ask(what):
    todo.append(what)
    print("for Goobs: " + what, flush=True)


class App:
    def __init__(self, name, port):
        self.name, self.base = name, f"http://127.0.0.1:{port}/api/v3/"
        xml = open(f"{APPDATA}/{name}/config.xml").read()
        self.key = re.search(r"<ApiKey>(.*?)</ApiKey>", xml).group(1)

    def __call__(self, path, method="GET", body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read().decode()
        except urllib.error.HTTPError as e:
            sys.exit(f"{self.name} {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
        return json.loads(data) if data.strip() else None


def min_age(app):
    cfg = app("config/indexer")
    if cfg.get("minimumAge") != MIN_AGE:
        note(f"{app.name}: Usenet minimum age {cfg.get('minimumAge')} -> {MIN_AGE} min")
        if not DRY:
            app("config/indexer", "PUT", {**cfg, "minimumAge": MIN_AGE})


def radarr_dead_root(radarr):
    for m in radarr("movie"):
        if m.get("path", "").startswith(OLD_ROOT + "/"):
            new = NEW_ROOT + m["path"][len(OLD_ROOT):]
            note(f"radarr: {m['title']} ({m.get('year')}) path {m['path']} -> {new}")
            if not DRY:
                radarr(f"movie/{m['id']}?moveFiles=false", "PUT", {**m, "path": new, "rootFolderPath": NEW_ROOT})
    for c in radarr("collection"):
        if c.get("rootFolderPath", "").startswith(OLD_ROOT):
            note(f"radarr: collection {c['title']} root folder -> {NEW_ROOT}")
            if not DRY:
                radarr(f"collection/{c['id']}", "PUT", {**c, "rootFolderPath": NEW_ROOT})


def radarr_blocked_imports(radarr):
    queue = radarr("queue?pageSize=500&includeUnknownMovieItems=true")["records"]
    seen, imported = set(), set()  # one pass per download; one import per movie (Help 2021 came twice)
    for q in sorted(queue, key=lambda r: r.get("size") or 0):
        if q.get("trackedDownloadState") != "importBlocked" or q.get("downloadId") in seen:
            continue
        seen.add(q.get("downloadId"))
        folder, movie_id = q.get("outputPath") or "", q.get("movieId")
        title = q.get("title", "")
        if not movie_id or not folder.startswith(DOWNLOADS) or movie_id in imported:
            ask(f"radarr queue: {title}: no movie match or path outside downloads ({folder}); decide in Activity > Queue")
            continue
        cands = radarr(f"manualimport?folder={urllib.parse.quote(folder)}&movieId={movie_id}&filterExistingFiles=true")
        files = [c for c in cands if VIDEO.search(c["path"]) and c["path"].startswith(folder.rstrip("/") + "/")]
        ok = [c for c in files if not c.get("rejections")]
        if len(files) != 1 or not ok:
            why = "; ".join(sorted({r.get("reason", "?") for c in files for r in c.get("rejections", [])})) or f"{len(files)} video files"
            ask(f"radarr queue: {title}: not imported ({why}); decide in Activity > Queue")
            continue
        f = ok[0]
        imported.add(movie_id)
        note(f"radarr: import {os.path.basename(f['path'])} ({(f.get('size') or 0) / 1e9:.1f} GB) as movie {movie_id} ({q.get('movie', {}).get('title', '?')})")
        if not DRY:
            radarr("command", "POST", {"name": "ManualImport", "importMode": "move", "files": [{
                "path": f["path"], "movieId": movie_id, "quality": f["quality"],
                "languages": f.get("languages") or [], "downloadId": q["downloadId"]}]})


def sonarr_deleted_series(sonarr):
    gone = [s for s in sonarr("series") if s.get("status") == "deleted"]
    faraway = sorted((s for s in gone if "faraway" in s["title"].lower() or s["title"].strip(": ") == "Australia"),
                     key=lambda s: -(s.get("statistics", {}).get("episodeFileCount", 0)))
    for s in faraway[1:]:
        files = s.get("statistics", {}).get("episodeFileCount", 0)
        if files:
            ask(f"sonarr: duplicate '{s['title']}' (id {s['id']}) has {files} files; merge by hand")
            continue
        note(f"sonarr: remove duplicate '{s['title']}' (id {s['id']}, no files; keeping '{faraway[0]['title']}' id {faraway[0]['id']})")
        if not DRY:
            sonarr(f"series/{s['id']}?deleteFiles=false&addImportListExclusion=false", "DELETE")
    for s in gone:
        if s not in faraway[1:]:
            ask(f"sonarr: '{s['title']}' was removed from TVDB ({s.get('statistics', {}).get('episodeFileCount', 0)} files, {s.get('path')}); keep (no more metadata) or delete it in Sonarr")


def main():
    radarr, sonarr = App("radarr", 7878), App("sonarr", 8989)
    for step in (lambda: min_age(radarr), lambda: min_age(sonarr),
                 lambda: radarr_dead_root(radarr), lambda: radarr_blocked_imports(radarr),
                 lambda: sonarr_deleted_series(sonarr)):
        step()
    print(f"{'would change' if DRY else 'changed'}: {len(changes)}; for Goobs: {len(todo)}")


if __name__ == "__main__":
    main()
