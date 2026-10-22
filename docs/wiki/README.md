# Home lab wiki

Everything about Goobs's home lab in one place: what runs, where, why, and how to operate it.
The wiki describes the lab as it stands on **2026-10-22**. The code in this repo is the source of
truth for configuration; when the wiki and the code disagree, the code wins and the wiki needs a fix.

A rendered, single-page version of this wiki is published as a Claude artifact (link in the project
thread "Home lab wiki"). Regenerate it from these files with `python3 docs/wiki/build_html.py <out.html>`.

## How to read it

| Start here if you want to... | Page |
| --- | --- |
| See the whole lab on one screen | [Overview](01-overview.md) |
| Know what the physical machines are and their quirks | [Hardware](02-hardware.md) |
| Understand how the pieces fit, with diagrams of request, data, API and alert flows | [Architecture](03-architecture.md) |
| Know which address, port and login reaches each app | [Networking and access](04-networking-and-access.md) |
| Know where files live and the rules about the NAS | [Storage](05-storage.md) |
| Read a deep dive on one service | [Services](services/README.md): [edge](services/edge.md), [media](services/media.md), [gpu](services/gpu.md), [apps](services/apps.md), [ai](services/ai.md), [TrueNAS and the hosts](services/truenas-and-hosts.md) |
| Know the state of the movie library, Tdarr and ErsatzTV projects | [Media library projects](06-media-library.md) |
| Understand why things were done this way | [Decision records](07-decisions.md) |
| Do something: deploy, reboot, recover from an outage, restore a backup | [Operations and runbooks](operations/README.md) |
| See what is still open or uncertain | [Open items](08-open-items.md) |
| Look up a term | [Glossary](09-glossary.md) |

## Conventions

- **Dates** are written as `2026-10-21`. Times in the thread history are UTC; the lab itself runs
  on Pacific time (`America/Los_Angeles`).
- **Names.** The two Proxmox hosts are *RicksGarage* (storage NUC) and *RicksShip* (compute NUC).
  The five app VMs are *edge*, *media*, *gpu*, *apps* and *ai*. "The NAS" means the TrueNAS VM and
  its pool `DeepData`.
- **Addresses.** `https://<app>.tail9af84.ts.net` is an app's Tailscale name. `192.168.1.x:port` is
  its LAN address, which the LAN firewall only allows from the lab VMs unless the page says otherwise.
- **Uncertain facts** are marked `(unverified)` or listed in [Open items](08-open-items.md).
  Nothing in the wiki is invented to fill a gap; where a fact could not be confirmed from the repo,
  the design doc, the build history or memory, it says so.
- **No credentials.** The wiki never contains passwords, API keys, tokens or auth keys. It only says
  where they live. See [Access and credentials](operations/access-and-credentials.md).

## Sources this wiki was built from

- This repo (`tofu/`, `ansible/`, `stacks/`, `docs/`, `README.md`) as of the merge of PR #7.
- The design doc "Home lab app stack: infrastructure design" and the "Power outage plan" doc
  (Claude Docs in the project).
- The project files `homelab/*.md` (first-run notes, overnight build summary, Servarr migration plan,
  Tdarr plan).
- The full history of the "Proxmox app stack infrastructure" thread (2026-10-21 04:02 UTC to
  2026-10-22 01:53 UTC), which holds the reasoning behind most decisions.
- Project memory notes kept during the build.
- The change log with undo steps lives outside the repo, on the Mac at `~/HomeLab/changes-2026-10-21.md`.
