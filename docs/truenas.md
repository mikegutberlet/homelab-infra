# TrueNAS VM 110: settings changed by hand

TrueNAS (192.168.1.209, SCALE 23.10.2) is not managed by Ansible. Every change made to it is
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

Disk SMART self-tests run on the Proxmox host that owns the USB bridges, not in TrueNAS:
`ansible/playbooks/proxmox-smart.yml`.
