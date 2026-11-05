#!/usr/bin/env python3
"""Give qBittorrent 5.2 back its WebUI settings after the downgrade from 5.3 (2026-11-05).

5.3.0 moved the WebUI keys from `[Preferences] WebUI\\...` into their own `[WebUI]` section
(MigrationVersion 12). 5.2.4 reads the old place only, found no password, made a temporary one and
locked every arr app out. This copies each `[WebUI]` key to `[Preferences]` as `WebUI\\<key>` when it
is missing there; the `[WebUI]` section stays (harmless for 5.2, used again if 5.3 returns).
Run with qBittorrent stopped. `--dry-run` reports only. Prints nothing secret.
"""
import re
import sys

CONF = "/opt/appdata/qbittorrent/qBittorrent/qBittorrent.conf"
DRY = "--dry-run" in sys.argv

text = open(CONF).read()
section = re.search(r"^\[WebUI\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
if not section:
    sys.exit("no [WebUI] section: nothing to copy (already a 5.2 layout?)")
if "[Preferences]\n" not in text:
    text = text.rstrip("\n") + "\n\n[Preferences]\n"
missing = []
for line in section.group(1).splitlines():
    if "=" not in line:
        continue
    key = line.split("=", 1)[0]
    if not re.search(r"^WebUI\\" + re.escape(key) + "=", text, re.M):
        missing.append(line)
print(f"{'would add' if DRY else 'adding'} {len(missing)} key(s) under [Preferences]: "
      + ", ".join(l.split('=', 1)[0] for l in missing))
if missing and not DRY:
    text = text.replace("[Preferences]\n", "[Preferences]\n" + "".join(f"WebUI\\{l}\n" for l in missing), 1)
    open(CONF, "w").write(text)
