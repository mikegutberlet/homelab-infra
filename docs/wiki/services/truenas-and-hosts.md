# TrueNAS and the Proxmox hosts

## TrueNAS VM (110, RicksGarage, 192.168.1.209)

| | |
| --- | --- |
| Software | TrueNAS 25.10.7 (Goldeye), upgraded from 23.10.2 on 2026-10-26; rollback boot environment 25.04.2.6. Pool feature flags not upgraded on purpose. Hand-made changes and their undo: [`docs/truenas.md`](../../truenas.md) |
| VM | 16 GB RAM fixed, ballooning off (was 32 GB on a 31 GiB host). vCPU count from the inventory: 6 `(unverified after the change)`. `onboot=1`, startup order 1 with a 120 s delay, so it comes up before every other guest on RicksGarage |
| Disks | The five DAS disks attached by-id as virtual disks (scsi4 to scsi8 on `virtio-scsi-single`, one I/O thread each since 2026-10-26); the VM's own boot disk on host storage |
| Services | NFS (`/mnt/DeepData/NAS` and `ProxMox` exports to 192.168.1.0/24), SMB, SSH (turned on 2026-10-21 for the read-only inventory key). The built-in UPS service (NUT client) is part of the UPS plan |
| Access | SSH as the user Goobs shared, with the Mac's `homelab_inventory_ed25519` key; the web UI with the credentials in the project's creds document. No service account exists yet; the design wants one scoped to the lab's datasets |
| Managed by this repo? | **No.** Nothing in `tofu/` or `ansible/` touches VM 110 or the pool. Folder creations and the one permission change were done by hand with Goobs's OK and are recorded in the change log |
| Monitoring | Kuma HTTPS check on the UI (digest); Scrutiny gets the disks' SMART from the host, not from TrueNAS; the OliveTin `zpool status` button is prepared but not wired |

What was changed on it during the build: RAM 32 to 16 GB with a clean restart (2026-10-21 05:30 UTC);
SSH enabled; folders `Media/Podcasts`, `Photos/Immich-uploads`, `Documents/Paperless/*`,
`Backups/homelab` and `to-delete/2026-10-21` created; `Media/NextCloud` (empty) chowned to 33:33 0770.
Nothing was deleted, moved or snapshotted.

## What runs on the Proxmox hosts themselves

The hosts stay close to stock. Three small things are managed by Ansible playbooks in this repo
(inventory group `proxmox`, user root):

| Playbook | What it installs | State |
| --- | --- | --- |
| `proxmox-igpu.yml` (RicksShip only) | `/etc/modprobe.d/vfio-igpu.conf` binding `8086:193b` to vfio-pci with `softdep i915 pre: vfio-pci`, then `update-initramfs`. Needs a cold restart | Applied 2026-10-21 17:33 UTC |
| `proxmox-scrutiny.yml` (both) | `/usr/local/bin/scrutiny-collector-metrics` v0.9.5 (sha256-pinned), `/etc/scrutiny/collector.yaml` pointing at `http://192.168.1.53:8087`, cron `15 4 * * *` | Applied 2026-10-21; nine disks reporting |
| `proxmox-heartbeat.yml` (both) | A cron every 5 minutes that curls a healthchecks.io ping URL (`heartbeat_url` per host in `hosts.yml`) | **Not active**: the URLs are placeholders until Goobs creates the two checks |

Host-level changes made by hand (all in the change log on the Mac): cluster node removal and
`two_node: 1`; `pve-no-subscription` repo and the 8.4 upgrade; `intel_iommu=on iommu=pt` and the vfio
modules in `/etc/modules`; `reboot=efi` on RicksGarage; `snippets` content type enabled on `local`
for cloud-init vendor data; the `tofu@pve` user, role and token; the Debian 12 generic image
downloaded to `local` on both hosts; `qm set 203 --hostpci0 ...` on RicksShip; `onboot`/startup
changes on VMs 110, 113 and 117.

## The old VMs (kept, powered off)

| VM | Host | IP | What it was | State |
| --- | --- | --- | --- | --- |
| 113 Servarr | RicksGarage | 192.168.1.14 | Ubuntu 22.04 Desktop; Sonarr, Radarr, Prowlarr, Bazarr, SABnzbd (port 6900, as `ubuntu`), Overseerr snap, an unclaimed Plex 1.32, PIA client; NFS at `/mnt/nfs`. Login user `ubuntu` | Services stopped and disabled, configs copied to `/root/migration-20261021`, `storage` added to the `ubuntu` group (undo: `gpasswd -d storage ubuntu`), shut down 2026-10-21 21:10 UTC, `onboot=0`. 8 GB RAM |
| 117 Plex ("UbuntuDev2") | RicksShip | 192.168.1.15 | Plex 1.43.5 snap as root, Tautulli snap; no hardware transcoding; DNS broken (IPv6-only resolver); system disk 70% full. Login user `ubutnu` (sic) | Plex stopped and disabled, data untouched; `qemu-guest-agent` installed from copied .debs; shut down 2026-10-21 18:48 UTC, `onboot=0`. Was 26 GB RAM (12 GB at the end) |

Rollback to either takes about 2 to 5 minutes (stop the new containers, start the old VM and its
services). Goobs decides around **2026-10-28** whether to delete them. The passwords for these two
VMs were posted in chat on 2026-10-21 and should be changed or the VMs deleted.

Other leftovers on the hosts, untouched: stopped VMs 100, 101, 104, 107, 108, 109, 114, 118 and
templates 105, 112, 115. Known issues: 100 and 109 share a MAC address; 112 and 115 point at missing
storage; 118 points at the disconnected Sabrent enclosure. Clean up only if Goobs asks.

## Credentials and tokens on the hosts

- `tofu@pve` with a role limited to VM creation and the token `tofu@pve!homelab`; the secret lives
  only on the Mac in `~/.config/homelab/proxmox-tofu.env`.
- Root SSH with the Mac's inventory key on both hosts. Password login remains possible (the root
  passwords are in the project's creds document and should be rotated now that the build is done).
- 2FA on the Proxmox root accounts was in the design and is `(not yet set up)`.
