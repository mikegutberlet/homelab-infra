#!/usr/bin/env python3
"""Remove Chaptarr's duplicate library rows (playbooks/chaptarr-dedupe.yml).

Chaptarr 0.9.965 creates one book row per edition and metadata source (hc:/gr:/az: ids) when
it refreshes an author, so one work shows up many times ("1984" x42 on 2026-11-03, 13,327 rows
in all). Upstream bugs Chaptarr/chaptarr#111 and #323, no fixed release yet, so this may need
running again after a later refresh brings them back.

Rows are grouped by author and title (titles with no Latin letters, e.g. Japanese or bare
numbers, are compared as written so they cannot merge). In each group every row with files stays; if none has
files, one row stays (a monitored ebook row first, then the lowest id). The rest are deleted
from Chaptarr's database only: DELETE /api/v1/book/{id}?deleteFiles=false, nothing on disk is
touched. `--dry-run` prints the counts and the first groups and deletes nothing. Runs as root on
the media VM; the API key is read from Chaptarr's config file and never printed.
"""
import json
import re
import sys
import urllib.error
import urllib.request

DRY = "--dry-run" in sys.argv
BASE = "http://127.0.0.1:8789/api/v1/"
KEY = re.search(r"<ApiKey>(.*?)</ApiKey>", open("/opt/appdata/chaptarr/config.xml").read()).group(1)


def api(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method, headers={"X-Api-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = r.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")
    return json.loads(data) if data.strip() else None


def norm(text):
    text = re.sub(r"\([^)]*\)", " ", (text or "").lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and "))
    return re.sub(r"^(the|a|an) ", "", " ".join(text.split()))


def key(title):
    """Group key: the normalised title, or the raw title when normalising leaves nothing to compare
    (non-Latin titles, bare numbers) so a whole author's books cannot collapse into one group. The
    first --check run (2026-11-04) had '' groups of 150 rows that would have gone."""
    n = norm(title)
    return n if re.search(r"[a-z]", n) else " ".join((title or "").lower().split())


def has_files(b):
    return bool(b.get("hasFiles")) or (b.get("statistics") or {}).get("bookFileCount", 0) > 0


def to_delete(rows):
    """Rows of one author+title group that go; the kept ones are the rest."""
    if len(rows) < 2:
        return []
    keep = [b for b in rows if has_files(b)]
    if not keep:
        keep = [min(rows, key=lambda b: (not (b.get("monitored") and str(b.get("mediaType")).lower() == "ebook"),
                                         b["id"]))]
    return [b for b in rows if b not in keep]


def main():
    books = api("book")
    groups = {}
    for b in books:
        if not key(b.get("title")):
            continue  # no title to compare on; left alone
        groups.setdefault((b.get("authorId"), key(b.get("title"))), []).append(b)
    doomed = {g: to_delete(rows) for g, rows in groups.items()}
    doomed = {g: d for g, d in doomed.items() if d}
    total = sum(len(d) for d in doomed.values())
    print(f"{len(books)} rows, {len(groups)} author+title groups, {len(doomed)} with duplicates, "
          f"{'would change' if DRY else 'changed'}: {total} rows")
    for g, d in sorted(doomed.items(), key=lambda x: -len(x[1]))[:15]:
        print(f"  {g[1]!r}: {len(d)} of {len(groups[g])} rows go")
    if DRY:
        return
    for d in doomed.values():
        for b in d:
            api(f"book/{b['id']}?deleteFiles=false&addImportListExclusion=false", "DELETE")
    print(f"done: {total} rows removed, no files touched")


if __name__ == "__main__":
    main()
