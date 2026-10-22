#!/usr/bin/env python3
"""Render docs/wiki/*.md into one self-contained HTML page.

    python3 docs/wiki/build_html.py out.html

Needs the `markdown` package (pip install markdown). Mermaid fences become
<pre class="mermaid"> blocks, which the Claude artifact viewer renders natively;
open the file in a plain browser and the diagrams show as source text.
"""
import html
import posixpath
import re
import sys
from pathlib import Path

import markdown

WIKI = Path(__file__).resolve().parent
REPO_BLOB = "https://github.com/mikegutberlet/homelab-infra/blob/main/"

# Reading order. Group headers appear in the sidebar only.
PAGES = [
    ("Start", "README.md"),
    (None, "01-overview.md"),
    (None, "02-hardware.md"),
    (None, "03-architecture.md"),
    (None, "04-networking-and-access.md"),
    (None, "05-storage.md"),
    ("Services", "services/README.md"),
    (None, "services/edge.md"),
    (None, "services/media.md"),
    (None, "services/gpu.md"),
    (None, "services/apps.md"),
    (None, "services/ai.md"),
    (None, "services/truenas-and-hosts.md"),
    ("Projects and decisions", "06-media-library.md"),
    (None, "07-decisions.md"),
    ("Operations", "operations/README.md"),
    (None, "operations/deploy-changes.md"),
    (None, "operations/reboot-a-host.md"),
    (None, "operations/power-outage.md"),
    (None, "operations/backups-and-restore.md"),
    (None, "operations/alerts.md"),
    (None, "operations/add-a-service.md"),
    (None, "operations/access-and-credentials.md"),
    (None, "operations/troubleshooting.md"),
    ("Reference", "08-open-items.md"),
    (None, "09-glossary.md"),
]

MERMAID_RE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
H1_RE = re.compile(r"^# (.+?)\s*$", re.M)


def slug_for(rel: str) -> str:
    rel = rel[:-3] if rel.endswith(".md") else rel
    if rel == "README":
        return "index"
    if rel.endswith("/README"):
        rel = rel[: -len("/README")]
    return re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")


SLUGS = {rel: slug_for(rel) for _, rel in PAGES}

# The single page has its own hero, so the index page gets a different heading,
# and the sidebar uses short labels for the long service-page titles.
TITLE_OVERRIDES = {"README.md": "About this wiki"}
NAV_LABELS = {
    "services/edge.md": "edge VM (.50)",
    "services/media.md": "media VM (.51)",
    "services/gpu.md": "gpu VM (.52)",
    "services/apps.md": "apps VM (.53)",
    "services/ai.md": "ai VM (.54)",
    "services/truenas-and-hosts.md": "TrueNAS and the hosts",
}


def rewrite_href(href: str, page_rel: str) -> str:
    """Point a Markdown link at the right in-page anchor or GitHub file."""
    if re.match(r"^[a-z]+:", href) or href.startswith("#"):
        if href.startswith("#"):
            return f"#{SLUGS[page_rel]}--{href[1:]}"
        return href
    path, _, frag = href.partition("#")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), path))
    if target in SLUGS:
        return f"#{SLUGS[target]}--{frag}" if frag else f"#{SLUGS[target]}"
    # Something in the repo outside the wiki.
    repo_path = posixpath.normpath(posixpath.join("docs/wiki", target))
    return REPO_BLOB + repo_path


def render_page(rel: str) -> dict:
    text = (WIKI / rel).read_text(encoding="utf-8")
    m = H1_RE.search(text)
    title = TITLE_OVERRIDES.get(rel, m.group(1) if m else rel)
    if m:
        text = text[: m.start()] + text[m.end():]

    diagrams = []

    def stash(match):
        diagrams.append(match.group(1))
        return f"\n\nMERMAIDBLOCK{len(diagrams) - 1}END\n\n"

    text = MERMAID_RE.sub(stash, text)

    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"],
                           extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(text)
    slug = SLUGS[rel]

    # Prefix heading ids with the page slug so pages never collide.
    body = re.sub(r'(<h[2-6]) id="([^"]+)"', lambda m: f'{m.group(1)} id="{slug}--{m.group(2)}"', body)
    body = re.sub(r'href="([^"]+)"', lambda m: f'href="{html.escape(rewrite_href(html.unescape(m.group(1)), rel))}"', body)
    body = re.sub(r"<p>MERMAIDBLOCK(\d+)END</p>",
                  lambda m: f'<div class="diagram"><pre class="mermaid">{html.escape(diagrams[int(m.group(1))])}</pre></div>',
                  body)
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
    body = re.sub(r"(?:<code>)?\(unverified\)(?:</code>)?", '<span class="unv">unverified</span>', body)

    sections = [(t["id"], t["name"]) for t in md.toc_tokens] if md.toc_tokens else []
    return {"rel": rel, "slug": slug, "title": title, "body": body,
            "sections": [(f"{slug}--{i}", n) for i, n in sections]}


CSS = r"""
:root {
  --bg: #f6f8f8; --surface: #ffffff; --surface-2: #eef2f3; --fg: #1a2326; --muted: #5b6b70;
  --border: #d5dde0; --accent: #0f7b8a; --accent-ink: #ffffff; --accent-soft: #dceef1;
  --code-bg: #eef2f3; --warn-bg: #fff1d6; --warn-fg: #6b4a00; --shadow: 0 1px 2px rgba(20,40,45,.08);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1517; --surface: #151d20; --surface-2: #1c2629; --fg: #e3eaec; --muted: #94a6ab;
    --border: #2a373b; --accent: #4cc3d2; --accent-ink: #06181b; --accent-soft: #15353b;
    --code-bg: #1c2629; --warn-bg: #3a2d10; --warn-fg: #f3d58a; --shadow: none; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1517; --surface: #151d20; --surface-2: #1c2629; --fg: #e3eaec; --muted: #94a6ab;
  --border: #2a373b; --accent: #4cc3d2; --accent-ink: #06181b; --accent-soft: #15353b;
  --code-bg: #1c2629; --warn-bg: #3a2d10; --warn-fg: #f3d58a; --shadow: none; color-scheme: dark;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 1rem; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 17px/1.55 "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 0 4rem;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3, h4 { font-family: "Bricolage Grotesque", "Avenir Next", "Segoe UI", system-ui, sans-serif; line-height: 1.15; text-wrap: balance; margin: 0; }
h2 { font-size: 1.45rem; margin: 2.4rem 0 .8rem; padding-top: .4rem; }
h3 { font-size: 1.15rem; margin: 1.8rem 0 .6rem; }
h4 { font-size: 1rem; margin: 1.4rem 0 .5rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; font-size: .82rem; }
p, ul, ol { margin: 0 0 1rem; max-width: 72ch; }
li { margin: .2rem 0; }
li > ul, li > ol { margin-bottom: .2rem; }
code, pre, kbd { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .86em; }
code { background: var(--code-bg); padding: .1em .35em; border-radius: 4px; overflow-wrap: anywhere; }
pre { background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px; padding: .9rem 1rem; overflow-x: auto; margin: 0 0 1.2rem; line-height: 1.45; }
pre code { background: none; padding: 0; font-size: .84rem; }
blockquote { margin: 0 0 1rem; padding: .6rem 1rem; border-left: 3px solid var(--accent); background: var(--surface); color: var(--muted); }
hr { border: 0; border-top: 1px solid var(--border); margin: 2rem 0; }
strong { font-weight: 650; }

.layout { max-width: 1240px; margin: 0 auto; display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 2.5rem; }
.side { position: sticky; top: env(safe-area-inset-top, 0px); align-self: start; max-height: calc(100vh - env(safe-area-inset-top, 0px)); overflow-y: auto; padding-block: 1.5rem; }
.side details > summary { display: none; }
.side .brand { font-family: "Bricolage Grotesque", system-ui, sans-serif; font-weight: 700; font-size: 1.1rem; margin-bottom: 1rem; display: block; color: var(--fg); }
.side .group { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 1.1rem 0 .3rem; }
.side ol { list-style: none; margin: 0; padding: 0; }
.side li a { display: block; padding: .22rem .5rem; border-radius: 6px; color: var(--fg); font-size: .93rem; }
.side li a:hover { background: var(--surface-2); text-decoration: none; }
.side li a.active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }

main { min-width: 0; padding-block: 1.5rem; }
.hero { padding: 1.4rem 0 1.6rem; border-bottom: 1px solid var(--border); margin-bottom: 1rem; }
.hero h1 { font-size: clamp(1.9rem, 3.5vw, 2.6rem); font-weight: 700; }
.hero p { color: var(--muted); margin: .6rem 0 0; }
.hero .meta { display: flex; flex-wrap: wrap; gap: .5rem 1.2rem; margin-top: .9rem; font-size: .85rem; color: var(--muted); }
.hero .meta span b { color: var(--fg); font-weight: 600; }

section.page { padding: 1.2rem 0 1.6rem; border-bottom: 1px solid var(--border); }
section.page:last-child { border-bottom: 0; }
.page-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .4rem 1rem; margin: 1.2rem 0 .4rem; }
.page-head h1 { font-size: clamp(1.6rem, 2.8vw, 2.1rem); font-weight: 700; }
.page-head .path { font-family: "JetBrains Mono", ui-monospace, monospace; font-size: .74rem; color: var(--muted); background: var(--surface-2); padding: .15em .5em; border-radius: 4px; }

.table-wrap { overflow-x: auto; margin: 0 0 1.3rem; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); box-shadow: var(--shadow); }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { text-align: left; vertical-align: top; padding: .5rem .7rem; border-bottom: 1px solid var(--border); }
th { background: var(--surface-2); font-weight: 650; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td code { white-space: nowrap; }

.diagram { overflow-x: auto; margin: 0 0 1.4rem; padding: 1rem; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); box-shadow: var(--shadow); }
.diagram pre.mermaid { background: none; border: 0; padding: 0; margin: 0; overflow: visible; font-size: .8rem; color: var(--muted); }

.unv { display: inline-block; font-size: .72rem; font-weight: 600; letter-spacing: .03em; text-transform: uppercase; background: var(--warn-bg); color: var(--warn-fg); padding: .05em .45em; border-radius: 999px; vertical-align: middle; }

.totop { position: fixed; right: max(16px, env(safe-area-inset-right, 0px)); bottom: calc(16px + env(safe-area-inset-bottom, 0px)); background: var(--accent); color: var(--accent-ink); border-radius: 999px; padding: .5rem .9rem; font-size: .85rem; font-weight: 600; box-shadow: 0 2px 8px rgba(0,0,0,.2); opacity: 0; pointer-events: none; transition: opacity .2s; }
.totop.show { opacity: 1; pointer-events: auto; }
.totop:hover { text-decoration: none; }

@media (max-width: 900px) {
  .layout { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .side { position: static; max-height: none; padding-block: 1rem 0; border-bottom: 1px solid var(--border); }
  .side details > summary { display: block; cursor: pointer; font-weight: 600; padding: .4rem 0; font-family: "Bricolage Grotesque", system-ui, sans-serif; }
  .side .brand { display: none; }
  body { font-size: 16px; }
}
"""

JS = r"""
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.side a[data-page]'));
  var byId = {};
  links.forEach(function (a) { byId[a.getAttribute('data-page')] = a; });
  var current = null;
  function setActive(id) {
    if (id === current) return;
    current = id;
    links.forEach(function (a) { a.classList.toggle('active', a.getAttribute('data-page') === id); });
  }
  var sections = Array.prototype.slice.call(document.querySelectorAll('section.page'));
  function onScroll() {
    var y = window.scrollY + 120, best = sections[0];
    for (var i = 0; i < sections.length; i++) { if (sections[i].offsetTop <= y) best = sections[i]; }
    if (best) setActive(best.id);
    var t = document.querySelector('.totop');
    if (t) t.classList.toggle('show', window.scrollY > 800);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  var det = document.querySelector('.side details');
  if (det && window.innerWidth <= 900) det.removeAttribute('open');
  if (det) det.addEventListener('click', function (e) {
    if (e.target.tagName === 'A' && window.innerWidth <= 900) det.removeAttribute('open');
  });
})();
"""


def build() -> str:
    pages = [render_page(rel) for _, rel in PAGES]
    groups = [g for g, _ in PAGES]

    nav = []
    for (group, _), p in zip(PAGES, pages):
        if group:
            if nav:
                nav.append("</ol>")
            nav.append(f'<div class="group">{html.escape(group)}</div><ol>')
        label = NAV_LABELS.get(p["rel"], p["title"])
        nav.append(f'<li><a href="#{p["slug"]}" data-page="{p["slug"]}">{html.escape(label)}</a></li>')
    nav.append("</ol>")

    body = []
    for p in pages:
        body.append(
            f'<section class="page" id="{p["slug"]}">'
            f'<div class="page-head"><h1>{html.escape(p["title"])}</h1>'
            f'<a class="path" href="{REPO_BLOB}docs/wiki/{p["rel"]}">docs/wiki/{p["rel"]}</a></div>'
            f'{p["body"]}</section>'
        )

    n_pages = len(pages)
    n_diagrams = sum(p["body"].count('class="mermaid"') for p in pages)
    n_services = 0
    for p in pages:
        if p["rel"].startswith("services/") and p["rel"] != "services/README.md":
            n_services += len(re.findall(r"<h3 ", p["body"]))

    return f"""<title>Home Lab Wiki</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="layout">
<aside class="side">
<a class="brand" href="#index">Home lab wiki</a>
<details open><summary>Contents</summary>
{''.join(nav)}
</details>
</aside>
<main>
<div class="hero">
<h1>Home lab wiki</h1>
<p>What runs in Goobs's lab, where it lives, why it was built this way, and how to operate it. Rendered from the Markdown pages under <code>docs/wiki/</code> in the homelab-infra repo.</p>
<div class="meta"><span>As of <b>2026-10-22</b></span><span><b>{n_pages}</b> pages</span><span><b>{n_diagrams}</b> diagrams</span><span><b>2</b> hosts, <b>6</b> VMs</span><span>Tailnet <b>tail9af84.ts.net</b></span></div>
</div>
{''.join(body)}
</main>
</div>
<a class="totop" href="#index">Top</a>
<script>{JS}</script>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("homelab-wiki.html")
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
