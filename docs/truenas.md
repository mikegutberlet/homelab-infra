# TrueNAS VM 110: settings changed by hand

TrueNAS (192.168.1.209, SCALE 25.10.7 since 2026-10-26) is not managed by Ansible. Every change made to it is
listed here with its undo, so a rebuild or a rollback knows what to put back. Pool, datasets
and shares are never touched by automation (see the README rules).

Survey of the storage setup, 2026-10-22: raw output on the Mac at
`~/HomeLab/storage-assessment-2026-10-22.txt`; plan in the "TrueNAS storage plan" doc in the
Claude project.

| Date | Change | Why | Undo |
| --- | --- | --- | --- |
| 2026-10-22 | ARC cap raised from the default 50% of RAM (7.8 GiB) to 9.5 GiB (10200547328 bytes: cache + free memory minus a 2 GiB reserve): live write to `/sys/module/zfs/parameters/zfs_arc_max`, kept across reboots by a Post Init command (System Settings > Advanced > Init/Shutdown Scripts, comment "homelab-infra: ARC cap", ID 1) | More read cache for about 30 containers on NFS once the old Emby app inside TrueNAS was stopped. From 24.04 on, ZFS would otherwise take almost all of the VM's RAM, so the same command then acts as a ceiling | Delete the Post Init command and write `8388608000` (or `0` for the version default) to the same sysfs file; takes effect at once |
| 2026-10-22 | `recordsize=1M` on `DeepData/NAS` (was the 128K default; every other dataset still 128K). Set while applying the no-downtime steps; Goobs chose to keep it | New large files (Tdarr output, downloads, photos) get 1 MB blocks: fewer blocks to track, faster future scrubs and rebuilds of those files. Costs a little extra disk work on partly written blocks while SABnzbd and qBittorrent download into the same dataset. Existing files are unchanged | `zfs inherit recordsize DeepData/NAS`; affects new writes only, any time |
| 2026-10-22 | Old Emby app (k3s) stopped by Goobs | The Emby in use runs on the gpu VM (192.168.1.52); the copy inside TrueNAS burned about a core and part of the 2.5 GiB the NAS had left | Start the app again on the Apps page. It disappears for good at the 24.10 upgrade; its data stays in `ix-applications` until deleted on purpose |
| 2026-10-26 | VM 110 disks: controller `virtio-scsi-pci` changed to `virtio-scsi-single` with `iothread=1` on scsi4 to scsi8 (`cache=none`, `aio=io_uring` kept). Done on the Proxmox host with the VM stopped, so no by-id disk was hot-unplugged | Each of the five disks gets its own I/O thread instead of sharing the VM's main loop, which helps scrubs and parallel reads | With the VM stopped: `qm set 110 --scsihw virtio-scsi-pci` and drop `iothread=1` from each disk line (old config backed up to the Mac as `110.conf`) |
| 2026-10-26 | Upgraded 23.10.2 > 24.04.2.5 > 24.10.2.4 > 25.04.2.6 > 25.10.7, one restart per step (about 10 minutes of NAS outage in total, 16:38 to 17:18 UTC). Pool feature flags were **not** upgraded; the "new ZFS features available" alert is expected. Pool, NFS shares, ARC cap and Post Init command all checked after each step | 23.10 was end of life | Activate the kept boot environment and restart: `midclt call boot.environment.activate '{"id":"25.04.2.6"}'`. Config backup from before the window (`freenas-v1.db`, `pwenc_secret`) on the Mac in `~/HomeLab/backups/truenas-20261026` |
| 2026-10-26 | Boot environments 23.10.0.1, 23.10.1.3 and 23.10.2 deleted (Goobs's choice) to make room on the small boot disk; left: 25.10.7 (active), 25.04.2.6, Initial-Install | The boot disk could not hold four upgrade images | None needed; a 23.10 rollback would mean a fresh install plus the config backup |
| 2026-10-26 | The "homepage" API key was revoked by the 25.04 step (25.04 revokes keys that carried an allow-list) | TrueNAS security change | Make a new one with `python3 docs/homepage_keys.py`, commit, apps stacks play, then `python3 docs/sysadmin_deploy.py` (n8n reads it from the apps VM's `.env`) |
| 2026-10-28 | VM 110: CPU type `x86-64-v2-AES` changed to `host` and RAM 16 to 20 GB (`qm set 110 --cpu host --memory 20480` after a clean `qm shutdown`, about 2 minutes of NAS outage at 17:43 UTC; Tdarr stopped first). ARC cap raised from 9.5 to 13.5 GiB (14495514624 bytes), written live and in the POSTINIT init script (id 1). Config backup `/root/110.conf.bak-20261028` on RicksGarage | `x86-64-v2-AES` hid AVX2 from the guest, so ZFS checksums and RAID-Z parity ran on the SSE code paths (avx2 count in `/proc/cpuinfo` 0 before, 6 after). RicksGarage had 9.9 GB free once media moved to RicksPortalGun; ARC hit rate was 92% at 9.5 GiB. Performance and power review of 2026-10-28 | With the VM stopped: `cp /root/110.conf.bak-20261028 /etc/pve/qemu-server/110.conf`, then set the init script and `zfs_arc_max` back to 10200547328 |

The REST API (`/api/v2.0`) used by Homepage and the n8n sysadmin is deprecated in 25.x and removed in
TrueNAS 27; stay on 25.10 until both move to the JSON-RPC API.

Disk SMART self-tests run on the Proxmox host that owns the USB bridges, not in TrueNAS:
`ansible/playbooks/proxmox-smart.yml`.
