"""Send every book on Goobs's Hardcover "Want to Read" list to Chaptarr (ebook) and Listenarr (audiobook).

Goodreads retired its shelf RSS feeds and its API, so Hardcover holds the reading list (the Goodreads
library was imported there once, 2026-11-02). Every INTERVAL_MIN minutes this reads the list and, for
each book not yet in an app, looks it up and adds it monitored with a search, so it is grabbed.

The apps are the state: a book already in Chaptarr (same foreign book id) or Listenarr (same ASIN,
the add answers 409) is skipped, so the first pass takes the whole list and later passes only see
books added since. A book removed from an app while it is still on Want to Read is added again;
take it off the list in Hardcover instead. No state file, no /opt/appdata folder.

Matching is strict: the title (before any subtitle) must match and one author's last name must
appear. No match is logged as "no match" and retried after RETRY_HOURS; nothing is guessed.
DRY_RUN=true logs what would be added and adds nothing; ONCE=true runs one pass and exits.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HARDCOVER = "https://api.hardcover.app/v1/graphql"
CHAPTARR, LISTENARR = "http://chaptarr:8789/api/v1", "http://listenarr:4545/api/v1"
INTERVAL_MIN = int(os.environ.get("INTERVAL_MIN", "60"))
RETRY_HOURS = int(os.environ.get("RETRY_HOURS", "24"))
IDLE_WAIT_MIN = int(os.environ.get("IDLE_WAIT_MIN", "30"))  # longest wait for Chaptarr to go idle
BOOKS_ROOT = os.environ.get("BOOKS_ROOT", "/mnt/nfs/Media/Books")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# Audible editions Goobs does not want (e.g. a dramatization); a book whose match is one of these is
# left out of Listenarr, to be added by hand if wanted.
SKIP_ASINS = {x.strip() for x in os.environ.get("LISTENARR_SKIP_ASINS", "").split(",") if x.strip()}
UA = "homelab hardcover-sync (github.com/mikegutberlet/homelab-infra)"

WANT_TO_READ = """
query { me { user_books(where: {status_id: {_eq: 1}}, order_by: {date_added: asc}) {
  book_id
  book { title contributions { contribution author { name } } }
} } }"""

misses = {}  # (app, hardcover book id) -> time of the last miss; retried after RETRY_HOURS


def call(url, headers, body=None, method=None, timeout=120):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data) if data else None


def norm(text, main=False):
    text = re.sub(r"\([^)]*\)", " ", (text or "").lower())  # "(Sprawl, #1)", "(Unabridged)"
    if main:
        text = text.split(":")[0]  # main title only, without the subtitle
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and "))
    return re.sub(r"^(the|a|an) ", "", " ".join(text.split()))


def matches(want, title, authors):
    # The app's title must be the whole wanted title, or its main part ("2001" for "2001: A Space
    # Odyssey"); never the other way round, or "The Lord of the Rings" matches "The Lord of the
    # Rings: The Return of the King" (first dry run, 2026-11-02).
    if norm(title) not in (norm(want["title"]), norm(want["title"], main=True)):
        return False
    names = " ".join(a.lower() for a in authors if a)
    return any(a.split()[-1].lower() in names for a in want["authors"] if a.split())


def want_to_read():
    data = call(HARDCOVER, {"Authorization": f"Bearer {os.environ['HARDCOVER_TOKEN']}"}, {"query": WANT_TO_READ})
    if data.get("errors"):
        raise RuntimeError(f"hardcover: {data['errors']}")
    books = []
    for ub in data["data"]["me"][0]["user_books"]:
        authors = [c["author"]["name"] for c in ub["book"]["contributions"]
                   if c.get("author") and c.get("contribution") in (None, "Author")]
        books.append({"id": ub["book_id"], "title": ub["book"]["title"], "authors": authors})
    return books


def due(app, book):
    last = misses.get((app, book["id"]))
    return last is None or time.time() - last > RETRY_HOURS * 3600


def label(book):
    return f"{book['title']} ({', '.join(book['authors']) or 'no author'})"


class Chaptarr:
    """Chaptarr keeps one row per book and format. The add mirrors its own Add New page (0.9.965):
    /api/v1/search (book/lookup?mediaType=ebook drops every remote result, Chaptarr issue #67), then
    POST /api/v1/book with mediaType ebook, the ebook root folder and profiles, "only this book"."""

    name = "chaptarr"

    def __init__(self):
        self.h = {"X-Api-Key": os.environ["CHAPTARR_API_KEY"]}
        rows = [b for b in call(f"{CHAPTARR}/book", self.h) if str(b.get("mediaType", "")).lower() == "ebook"]
        self.have = {str(b.get("foreignBookId") or b.get("foreignId")) for b in rows}
        # Also by title: Chaptarr 0.9.965 keeps a book under a different work id (hc:/gr:/az:) than
        # the one its search returns, so Snow Crash and Starship Troopers were "added" every hour
        # (2026-11-03, 10 passes each) although the file was already there.
        self.titles = {norm(b.get("title")) for b in rows}

    def wait_idle(self):
        # Each add makes Chaptarr load the author's whole catalogue in the background. Sending the next
        # request before that finishes left SQLite "database is locked", 120 s timeouts and a 500 on
        # the first live pass (2026-11-03, 21 of 33 failed), so wait until its command queue is empty.
        deadline = time.time() + IDLE_WAIT_MIN * 60
        while time.time() < deadline:
            busy = [c for c in call(f"{CHAPTARR}/command", self.h) or []
                    if str(c.get("status", "")).lower() in ("queued", "started")]
            if not busy:
                return
            time.sleep(15)
        raise RuntimeError(f"still busy after {IDLE_WAIT_MIN} min")

    def add(self, book):
        self.wait_idle()
        term = urllib.parse.quote(f"{book['title']} {' '.join(book['authors'][:1])}")
        # goodreads, as its Add New page searches here: the hardcover provider needs a Hardcover token
        # in Chaptarr's own settings and otherwise answers 503 "Hardcover search failed".
        found = call(f"{CHAPTARR}/search?term={term}&provider=goodreads", self.h) or []
        hit = None
        for r in found:
            b = r.get("book")
            if b and matches(book, b.get("title"), [(b.get("author") or r.get("author") or {}).get("authorName")]):
                hit = dict(b, author=b.get("author") or r.get("author"))
                break
        if hit is None:
            return "no match"
        fid = hit.get("foreignId") or hit.get("foreignBookId") or hit.get("hardcoverBookId")
        if str(hit.get("foreignBookId") or fid) in self.have or norm(hit.get("title")) in self.titles:
            return "already there"
        if DRY_RUN:
            return f"would add \"{hit.get('title')}\" by {hit['author'].get('authorName')} ({fid})"
        root = next(r for r in call(f"{CHAPTARR}/rootfolder", self.h) if r["path"].rstrip("/") == BOOKS_ROOT)
        ebook = root.get("ebook") or {}
        quality = root.get("ebookQualityProfileId") or ebook.get("qualityProfileId")
        metadata = root.get("ebookMetadataProfileId") or ebook.get("metadataProfileId")
        if not quality or not metadata:
            raise RuntimeError(f"root folder {BOOKS_ROOT} has no ebook quality/metadata profile")
        hit["author"].update({
            "metadataProfileId": metadata, "ebookMetadataProfileId": metadata, "ebookQualityProfileId": quality,
            "ebookRootFolderPath": root["path"], "ebookMonitored": True, "ebookMonitorNewItems": "none",
            "ebookTags": [], "tags": [], "monitored": True,
            # Only this book: the author's other books stay unmonitored.
            "addOptions": {"monitor": "specificBook", "booksToMonitor": [fid], "searchForMissingBooks": False},
        })
        hit.update({"id": 0, "localBookId": None, "mediaType": "ebook", "monitored": True,
                    "ebookMonitored": True, "audiobookMonitored": False,
                    "addOptions": {**(hit.get("addOptions") or {}), "searchForNewBook": True}})
        editions = hit.get("editions") or [{"title": hit.get("title"), "overview": hit.get("overview") or ""}]
        if not any(e.get("monitored") for e in editions):
            editions[0].update({"monitored": True, "manualAdd": False})
        hit["editions"] = editions
        call(f"{CHAPTARR}/book", self.h, hit, timeout=600)
        self.have.add(str(hit.get("foreignBookId") or fid))
        self.titles.add(norm(hit.get("title")))
        return "added, search started"


class Listenarr:
    name = "listenarr"

    def __init__(self):
        self.h = {"X-Api-Key": os.environ["LISTENARR_API_KEY"]}

    def add(self, book):
        q = urllib.parse.quote(f"{book['title']} {' '.join(book['authors'][:1])}")
        found = (call(f"{LISTENARR}/search/audible?query={q}&region=us", self.h) or {}).get("results") or []
        # "Virtual Voice" is Audible's synthetic narration; take a human reading or nothing.
        hit = next((r for r in found if matches(book, r.get("title"), [a.get("name") for a in r.get("authors") or []])
                    and not any("virtual voice" in (n.get("name") or "").lower() for n in r.get("narrators") or [])), None)
        if hit is None:
            return "no match"
        if hit.get("asin") in SKIP_ASINS:
            return "skipped"
        try:
            call(f"{LISTENARR}/library/by-asin/{urllib.parse.quote(hit.get('asin') or '-')}", self.h)
            return "already there"
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        if DRY_RUN:
            narr = ", ".join(n.get("name") or "" for n in (hit.get("narrators") or [])[:2])
            return f"would add \"{hit.get('title')}\" read by {narr or '?'} (ASIN {hit.get('asin')})"
        series = (hit.get("series") or [{}])[0]
        meta = {
            "asin": hit.get("asin"), "source": "Audible", "region": "us",
            "title": hit.get("title"), "subtitle": hit.get("subtitle"),
            "authors": [a["name"] for a in hit.get("authors") or [] if a.get("name")],
            "narrators": [n["name"] for n in hit.get("narrators") or [] if n.get("name")],
            "imageUrl": hit.get("imageUrl"), "publisher": hit.get("publisher"), "language": hit.get("language"),
            "runtime": hit.get("runtimeLengthMin") or hit.get("lengthMinutes") or hit.get("runtimeMinutes"),
            "publishYear": (hit.get("releaseDate") or "")[:4] or None, "publishedDate": (hit.get("releaseDate") or "")[:10] or None,
            "series": series.get("name"), "seriesNumber": series.get("position"),
            "genres": [g["name"] for g in hit.get("genres") or [] if g.get("name")], "isbn": [],
        }
        try:
            call(f"{LISTENARR}/library/add", self.h, {"metadata": meta, "monitored": True, "autoSearch": True})
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return "already there"
            raise
        return "added, search started"


def run_pass():
    books = want_to_read()
    print(f"hardcover: {len(books)} books on Want to Read", flush=True)
    for app_cls in (Chaptarr, Listenarr):
        try:
            app = app_cls()
        except Exception as exc:  # one app down must not stop the other
            print(f"{app_cls.name}: unreachable: {exc}", file=sys.stderr, flush=True)
            continue
        for book in books:
            if not due(app.name, book):
                continue
            try:
                result = app.add(book)
            except Exception as exc:
                result = f"failed: {exc}"
            if result == "no match" or result.startswith("failed"):
                misses[(app.name, book["id"])] = time.time()
            else:
                misses.pop((app.name, book["id"]), None)
            if result not in ("already there", "skipped"):  # both quiet; the skip list prints at start
                print(f"{app.name}: {label(book)}: {result}{' (dry run)' if DRY_RUN else ''}", flush=True)


def main():
    if SKIP_ASINS:
        print(f"listenarr: skipping ASINs {', '.join(sorted(SKIP_ASINS))}", flush=True)
    once = os.environ.get("ONCE", "false").lower() == "true"
    while True:
        try:
            run_pass()
        except Exception as exc:  # keep running; the next pass retries
            print(f"pass failed: {exc}", file=sys.stderr, flush=True)
        if once:
            return
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
