# Open items

Everything that is pending, waiting on a decision, or known but not yet verified, as of
2026-10-22. Each item names who is holding it. Dates are UTC unless marked Pacific.

## Needs Goobs at the hardware

| Item | Why | Status |
| --- | --- | --- |
| Set **After Power Failure = Power On** in the BIOS of both NUCs | Without it the hosts stay off after a power cut until someone presses the button | Pending; needs a monitor and keyboard at each NUC (see [Power outage](operations/power-outage.md)) |
| Buy a UPS (about 1500 VA, USB data port) and run NUT | Clean shutdown on a long outage instead of a hard power loss for the NAS | Decision open; NUT configuration is not in the repo yet |
| Fix the warm-reboot hang (try `reboot=pci` / `reboot=efi` kernel parameters or a BIOS update) | Every host restart currently needs the power button | Not started; power-off restarts remain the rule |
| Decide what to do with old VMs **113** (Servarr) and **117** (Plex) | They are powered off and keep host disk; their passwords were typed into chat once | Goobs decides around **2026-10-28**; delete nothing before then |

## Needs Goobs's input

| Item | Why | Status |
| --- | --- | --- |
| healthchecks.io ping URLs for both hosts | The host heartbeat (`playbooks/heartbeat.yml`, `heartbeat_url` in `inventory/hosts.yml`) is the only out-of-band "the house lost power" signal | URLs not created yet; the playbook has not been run |
| wg-easy as a backup VPN next to Tailscale | Covers the case where Tailscale's coordination is unreachable | Deferred by Goobs; a port forward would be needed, which conflicts with the Tailscale-only rule |
| Rotate the passwords in the shared "IP's and Creds" document (Proxmox root, TrueNAS, old VMs) | The build is done and the design called for a rotation | Not done |
| 2FA on Authentik and on Proxmox | Admin pages are tailnet-only, so the risk is moderate | Not configured `(unverified)` |
| Rename the 337 movie folders written as `Title2026` to `Title (2026)` | Radarr cannot match them; the Tdarr full run waits behind this | Task known; not started |
| Review the `.aac`-only files and folders with several video files | They are either junk or unusual releases; Tdarr would otherwise process them | List exists in the Tdarr plan; no decision |
| The 76 4K files and 231 unmatched movie folders | No-4K rule says they should go or be replaced by 1080p copies; unmatched folders may be mis-named or junk | Awaiting review |
| 9 leftover folders (about 28 GB) in `sabnzb/complete` | Remains of the old failed-import backlog | Delete none without Goobs's OK |
| 21 entries under `/mnt/backupsmb` on the old VM | Unknown purpose | Review before the old VMs are removed |
| Plex from outside the house: the internet is Starlink, whose IPv4 is carrier NAT (router WAN 100.82.x, public 98.97.x; Goobs 2026-10-26: "it's always been starlink") | The 6996 forward cannot work through carrier NAT, so remote Plex apps (friends, phones off Wi-Fi without Tailscale) use the bandwidth-limited Plex relay. Plex's "mapped" on 2026-10-21 did not mean the forward worked | Decision open, only if remote viewing matters: Tailscale on the remote devices, a public IPv4 from Starlink (business plans), or keep the relay |
| Empty the holding folder `to-delete/2026-10-21` | 14-day review window for duplicate and junk removals | About **2026-11-04**, after Goobs confirms nothing is missing |

## In progress or scheduled

| Item | Status |
| --- | --- |
| GingaDADDY indexer | Disabled in Prowlarr with settings kept and a 100 grabs/day limit; re-enable about **2026-10-22 05:45 UTC**; if still capped, Goobs emails them |
| Tdarr full conversion run | Settings done per the plan; Tdarr's `Media` mount is read-write and it also mounts `to-delete` since PR #8 (merged 2026-10-22); the run is on hold behind the folder-rename task above |
| ErsatzTV channel build | 11 channels chosen; building in the ErsatzTV thread |
| Wrong movie and TV cover art | Being handled in its own thread |
| Radarr relink and duplicate sorting | 850 missing movies relinked, 208 weaker copies moved to holding, 15 better copies imported, junk cleanup done; remaining unmatched folders listed above |

## Gaps in the automation (lab side, no decision needed)

| Gap | Effect | Fix |
| --- | --- | --- |
| Uptime Kuma has no monitors for ErsatzTV, Immich, Tdarr, Unmanic, wg-easy, the NFS mount or pool health | Failures there only show up in use | Append to `docs/kuma_setup.py` and re-run |
| Diun runs only on the apps VM | Image updates on edge, media, gpu and ai are not reported | Add Diun to each stack or a single Diun with Docker socket proxies |
| OliveTin is deployed but its actions are not wired to anything | It is a placeholder | Define the first safe actions (restart a container, check the mount) |
| The `ai` worker service is written but not started | No automation uses the LLM yet | Add it to `stack_services` on ai when there is a use |
| Scrutiny web crashes once at boot (InfluxDB not ready) | Cosmetic; autoheal restarts it | `depends_on` with a healthcheck |
| Homepage has no API widgets | Dashboard shows reachability only | Needs app API keys moved into the secrets file |
| `grub-efi` not pinned on the VMs | Cloud image default; a future image change could alter boot | Verify once, note in the base role |
| Backups have a single copy (on the NAS) and no whole-VM images | A pool loss takes the backups with it | Proxmox Backup Server and an off-site copy, per the design's next steps |
| NFS export is open to the whole `192.168.1.0/24` | Any LAN device can mount the share | Tighten to `192.168.1.50-54` and the hosts |
| `Photos` has no second copy | Phone backups now land there via Immich | Part of the off-site decision |
| TrueNAS VM vCPU count | Not recorded in the repo (managed by hand) | Record it `(unverified)` |
| Stale Tailscale nodes from the old VMs | Clutter and leftover keys | Remove in the Tailscale admin console |
| Tailscale reusable key expiry (about **2027-01-19**) | TSDProxy can no longer register new names after that; existing ones keep working for a while | Rotation steps in [Access and credentials](operations/access-and-credentials.md) |

## First-run steps not yet confirmed done

These were handed to Goobs; the wiki assumes they are **not** done until confirmed.

- Immich: admin account and the external library pointing at `Photos` (read-only).
- AnythingLLM: first-run wizard (workspace, Gemini model choice).
- Huginn: change the generated `admin` password in-app.
- Storyteller: create the first account.
- RomM: IGDB client credentials for metadata.
- Open WebUI and AnythingLLM: confirm the Gemini key works after the key file was moved to encrypted storage.
- Scrutiny: confirm the daily 04:15 collector run on both hosts shows all 9 disks.

## Unverified facts used in this wiki

Marked `(unverified)` where they appear. The main ones:

- Router model, DHCP range and whether the lab IPs are reserved there
  ([Hardware](02-hardware.md), [Networking](04-networking-and-access.md)).
- Whether Authentik enforces any second factor.
- Exact Tailscale key creation date (expiry derived from it).
- TrueNAS VM vCPU allocation and the exact DAS enclosure model.
