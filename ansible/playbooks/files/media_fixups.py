#!/usr/bin/env python3
"""One-off repairs found by the 2026-10-28 download-path audit (playbooks/media-fixups.yml).

Runs as root on the media VM; `--dry-run` only reports. Every step reads first and writes only
when something differs, so a second run changes nothing. Files on disk are deleted only for
failed downloads (never from the library), and `--dry-run` lists each one first.

  Radarr/Sonarr  minimum age 15 min for Usenet grabs: fresh posts failed as "cannot be
                 completed" before all articles had propagated (Terminator 3, Matchbox, 10-27)
  Radarr         14 movies still point at the old Servarr VM's SMB path
                 (/mnt/backupsmb/DeepData/Media/Movies): repoint them (and the collection that
                 used it) at /mnt/nfs/Media/Movies, no files moved; clears the two health errors
                 and lets the backlog search find them
  Radarr         queue items stuck on "manual import required" / import failed: import the one
                 video file of the download when Radarr itself knows the movie (movieId from its
                 own grab record) and has no objection to the file; otherwise (no video file,
                 only archives, samples, multi-part, rejected) the download is removed from the
                 queue and the client with its files, the release is blocklisted and Radarr
                 searches for another one; a download Radarr lost the movie link for is matched
                 again from its release name, or removed with its files when no library movie fits
  qBittorrent    incomplete torrents on the scratch disk (/incomplete/torrents, compose volume
                 /srv/usenet-incomplete) instead of the NAS: a 1 MB/s torrent was ~70 small NFS
                 sync writes a second that kept all five NAS disks busy around the clock; the
                 save path stays on /mnt/nfs so imports are still hardlinks
  Sonarr         series TVDB removed (status "deleted", no metadata or searches ever again, one of
                 them added eight times under "Faraway Downs" names): remove them from Sonarr,
                 files stay on disk, import lists may not re-add them
  qBittorrent    torrents in the audiobooks category that carry no audio file (Listenarr grabbed
                 EPUB/PDF releases that can never import): deleted with their files

The API keys are read from the apps' config files and never printed.
"""
import json
import os
import re
import subprocess
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


def walk(folder):
    if os.path.isfile(folder):
        return [(folder, os.path.getsize(folder))]
    return sorted(((os.path.join(d, f), os.path.getsize(os.path.join(d, f)))
                   for d, _, fs in os.walk(folder) for f in fs), key=lambda x: -x[1])


def radarr_blocked_imports(radarr):
    queue = radarr("queue?pageSize=500&includeUnknownMovieItems=true")["records"]
    seen, imported = set(), set()  # one pass per download; one import per movie (Help 2021 came twice)
    for q in sorted(queue, key=lambda r: r.get("size") or 0):
        if q.get("trackedDownloadState") not in ("importBlocked", "importFailed") or q.get("downloadId") in seen:
            continue
        seen.add(q.get("downloadId"))
        folder, movie_id = q.get("outputPath") or "", q.get("movieId")
        title, movie = q.get("title", ""), (q.get("movie") or {}).get("title", "?")
        why = "; ".join(m for sm in q.get("statusMessages", []) for m in sm.get("messages", [])) or q.get("errorMessage") or "?"
        if folder and not folder.startswith(DOWNLOADS):
            ask(f"radarr queue: {title}: path outside downloads ({folder}); decide in Activity > Queue")
            continue
        if not movie_id:  # Radarr lost the link to the movie: find it again from the release name
            movie_id = ((radarr(f"parse?title={urllib.parse.quote(title)}") or {}).get("movie") or {}).get("id")
            if movie_id:
                movie = radarr(f"movie/{movie_id}")["title"]
            else:
                note(f"radarr: remove {title} with its files (no movie in the library matches it)")
                if not DRY:
                    radarr(f"queue/{q['id']}?removeFromClient=true&blocklist=false&skipRedownload=true", "DELETE")
                continue
        if movie_id in imported:
            continue
        files = walk(folder) if folder and os.path.exists(folder) else []
        if DRY:
            print(f"  {title} -> {movie}: {why}", flush=True)
            for path, size in files[:8]:
                print(f"    {size / 1e9:6.2f} GB {path[len(DOWNLOADS):]}", flush=True)
        cands = radarr(f"manualimport?folder={urllib.parse.quote(folder)}&movieId={movie_id}&filterExistingFiles=false") if files else []
        videos = [c for c in cands if VIDEO.search(c["path"]) and c["path"].startswith(folder.rstrip("/") + "/")]
        ok = [c for c in videos if not c.get("rejections")]
        if len(videos) == 1 and ok:
            f = ok[0]
            imported.add(movie_id)
            note(f"radarr: import {os.path.basename(f['path'])} ({(f.get('size') or 0) / 1e9:.1f} GB) as {movie}")
            if not DRY:
                radarr("command", "POST", {"name": "ManualImport", "importMode": "move", "files": [{
                    "path": f["path"], "movieId": movie_id, "quality": f["quality"],
                    "languages": f.get("languages") or [], "downloadId": q["downloadId"]}]})
            continue
        reason = "; ".join(sorted({r.get("reason", "?") for c in videos for r in c.get("rejections", [])})) or \
            (f"{len(videos)} video files" if videos else "no video file" + (" (download folder gone)" if not files else ""))
        note(f"radarr: remove {title} ({movie}: {reason}), blocklist it and search again")
        if not DRY:
            radarr(f"queue/{q['id']}?removeFromClient=true&blocklist=true&skipRedownload=false", "DELETE")


QBIT = "http://127.0.0.1:8085/api/v2"  # inside gluetun's namespace (login skipped for localhost)
QBIT_TEMP = "/incomplete/torrents"


AUDIO = re.compile(r"\.(m4b|mp3|m4a|flac|ogg|opus|aac|wav|wma)$", re.I)


def wget(path, post=None):
    gluetun = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=gluetun"],
                             capture_output=True, text=True).stdout.split()
    if not gluetun:
        sys.exit("gluetun is not running")
    cmd = ["docker", "exec", gluetun[0], "wget", "-qO-", "-T", "20"] + (["--post-data", post] if post else [])
    r = subprocess.run(cmd + [QBIT + path], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"qBittorrent {path}: wget failed ({r.stderr.strip()[:200]})")
    return r.stdout


def qbit_ebook_torrents():
    for t in json.loads(wget("/torrents/info?category=audiobooks")):
        files = json.loads(wget("/torrents/files?hash=" + t["hash"]))
        if not files or any(AUDIO.search(f["name"]) for f in files):
            continue
        kinds = sorted({os.path.splitext(f["name"])[1].lower() or "?" for f in files})
        note(f"qbittorrent: delete '{t['name']}' with its files (audiobooks category, {len(files)} files, only {' '.join(kinds)}, {t.get('state')})")
        if not DRY:
            wget("/torrents/delete", f"hashes={t['hash']}&deleteFiles=true")


def qbit_temp_path():
    if not os.path.isdir("/srv/usenet-incomplete"):
        sys.exit("/srv/usenet-incomplete is not mounted")
    prefs = json.loads(wget("/app/preferences"))
    if not prefs.get("temp_path_enabled") or prefs.get("temp_path", "").rstrip("/") != QBIT_TEMP:
        note(f"qbittorrent: incomplete torrents {prefs.get('temp_path')!r} (enabled={prefs.get('temp_path_enabled')}) -> {QBIT_TEMP}")
        if not DRY:
            wget("/app/setPreferences", "json=" + json.dumps({"temp_path_enabled": True, "temp_path": QBIT_TEMP}))


def sonarr_deleted_series(sonarr):
    for s in sorted((s for s in sonarr("series") if s.get("status") == "deleted"), key=lambda s: s["title"]):
        st = s.get("statistics", {})
        note(f"sonarr: remove TVDB-deleted '{s['title']}' (id {s['id']}, {st.get('episodeFileCount', 0)} files kept in {s.get('path')})")
        if not DRY:
            sonarr(f"series/{s['id']}?deleteFiles=false&addImportListExclusion=true", "DELETE")


def main():
    radarr, sonarr = App("radarr", 7878), App("sonarr", 8989)
    for step in (lambda: min_age(radarr), lambda: min_age(sonarr),
                 lambda: radarr_dead_root(radarr), lambda: radarr_blocked_imports(radarr),
                 lambda: sonarr_deleted_series(sonarr), qbit_temp_path, qbit_ebook_torrents):
        step()
    print(f"{'would change' if DRY else 'changed'}: {len(changes)}; for Goobs: {len(todo)}")


if __name__ == "__main__":
    main()
