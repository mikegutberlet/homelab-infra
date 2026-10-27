# Fill RicksPortalGun (transcode VM, then apps and media)

The plan from 2026-10-27 (Goobs chose "full plan, with the iGPU Tdarr worker"): RicksPortalGun
(192.168.1.38, 62.7 GiB) gets a transcode VM with its iGPU, then the apps and media VMs with more
RAM. Afterwards RicksGarage runs TrueNAS 16 + edge 4 GB (about 11 GB free) and RicksShip gpu 16 +
ai 6 GB (about 9 GB free). TrueNAS is not touched by any step here.

Run each step only on Goobs's go for that step, from `~/HomeLab/homelab-infra` on the Mac, and log
it with its undo in `~/HomeLab/changes-<date>.md`.

| Step | Downtime | Needs |
| --- | --- | --- |
| 1. Restart the empty host (new kernel, vfio, warm-reboot test, BIOS) | none | Goobs at the button |
| 2. Transcode VM (second Tdarr node) | none | step 1 |
| 3. The spare 525 GB SSD as `PortalGunSpare` (media's scratch disk) | none | Goobs's OK (2026-10-27: "not used for anything"); the command refuses a non-blank `sda` |
| 4. Move apps from RicksShip, then grow gpu and ai | apps 15-30 min; Plex/Emby ~2 min | step 1 |
| 5. Move media from RicksGarage | media 30-45 min (downloads pause) | step 1 |

**Disks (corrected 2026-10-27).** Proxmox is installed on the 1 TB MX500, `sdb`: its `pve` volume
group holds root, swap and the 794 GB `local-lvm` thin pool. The spare is the 525 GB MX300, `sda`.
Never run `wipefs`, `pvcreate` or `vgcreate` on `sdb`. `local-lvm` alone fits apps (96 GB), media
(64 + 200 GB) and transcode (48 GB), but media's 200 GB SABnzbd scratch disk goes to the spare
`sda` instead (Goobs, 2026-10-27), so download and unpack writes never compete with the VMs' system disks.

Migrate VMs **stopped**. Every VM uses CPU type `host`, and a live move between Skylake and
Coffee Lake can crash the guest.

## 1. Restart the empty host

Read-only check first; it must show no VMs, an Intel VGA device at 00:02.0, and whether
192.168.1.55 is free:

```sh
ssh root@192.168.1.38 'uname -r; qm list; lspci -nn -s 00:02.0; ls /sys/kernel/iommu_groups | wc -l;
  lsblk -o NAME,SIZE,MODEL,FSTYPE,MOUNTPOINT; ping -c2 -W1 192.168.1.55 >/dev/null && echo ".55 IN USE" || echo ".55 free"'
```

If the IOMMU group count is 0, stop: the kernel command line needs `intel_iommu=on iommu=pt`
first (as on the other hosts).

Bind the iGPU to vfio-pci (writes `/etc/modprobe.d/vfio-igpu.conf`, rebuilds the initramfs):

```sh
source ~/.local/share/homelab-ansible/bin/activate
cd ansible && ansible-playbook playbooks/proxmox-igpu.yml --limit ricksportalgun
```

Warm-reboot test, which also boots the new kernel and applies vfio:

```sh
ssh root@192.168.1.38 reboot
```

Wait 3 minutes. If `ping 192.168.1.38` answers, warm reboot works on this NUC (record it in
[Hardware](../02-hardware.md)). If not, it hangs like the NUC6s: press the power button.

BIOS (needs a monitor and keyboard on the NUC): `ssh root@192.168.1.38 'shutdown -h now'`, press
the button, tap F2, set *Power > Secondary Power Settings > After Power Failure* to *Power On*, save.

Check:

```sh
ssh root@192.168.1.38 'uname -r; lspci -nnk -s 00:02.0 | grep "in use"; pvecm status | grep -E "Quorate|Total votes"'
```

Expect `6.8.12-43-pve`, `Kernel driver in use: vfio-pci`, `Quorate: Yes`, 3 votes.

## 2. Transcode VM

VM 206 at 192.168.1.55: 4 cores, 4 GB, 48 GB on `local-lvm`, Debian 12 like the others.

```sh
cd ~/HomeLab/homelab-infra/tofu
set -a; . ~/.config/homelab/proxmox-tofu.env; set +a
tofu plan  -target='proxmox_virtual_environment_vm.vm["transcode"]'   # 1 VM + its image and snippet on RicksPortalGun, nothing else
tofu apply -target='proxmox_virtual_environment_vm.vm["transcode"]'
ssh root@192.168.1.38 'qm shutdown 206 --timeout 120 && qm set 206 --hostpci0 0000:00:02.0,pcie=1 && qm start 206'
cd ../ansible
ansible-playbook playbooks/site.yml --limit transcode --skip-tags stacks
ansible-playbook playbooks/site.yml --limit gpu --tags lanfw   # lets .55 reach the Tdarr server on 8266
```

Copy the flow's folder (flow.js, maps, API keys) from the gpu VM, then start the node:

```sh
ssh goobs@192.168.1.52 'sudo tar -C /opt/appdata/tdarr/configs -cf - hevc' \
  | ssh goobs@192.168.1.55 'sudo tar -C /opt/appdata/tdarr-node/configs -xpf -'
ansible-playbook playbooks/site.yml --limit transcode --tags stacks
ssh goobs@192.168.1.55 'vainfo 2>&1 | grep -i hevc | head -3; sudo docker logs --tail 20 transcode-tdarr-node-1'
```

In Tdarr (https://tdarr.tail9af84.ts.net), the node `transcode-vm` appears under Nodes. Set its
workers like `gpu-vm`'s: Transcode GPU 1, every other worker type 0. The library schedule (17:00-23:00
PT pause) and the 3 TB guard apply to both nodes.

Repeat the tar copy whenever `media_map.py` refreshes the maps or `flow.js` changes. This node's run
log is `/opt/appdata/tdarr-node/logs/hevc-run.tsv` on the transcode VM; progress checks read both.

Undo: pause or delete the node in Tdarr, `qm stop 206`, remove `transcode` from tfvars and hosts.yml.

## 3. The spare 525 GB SSD

First confirm which disk is booted and that `sda` holds nothing:

```sh
ssh root@192.168.1.38 'findmnt -n -o SOURCE /; pvs; lsblk -o NAME,SIZE,MODEL,FSTYPE,MOUNTPOINT /dev/sda; wipefs -n /dev/sda'
```

`/` must be on `pve-root`, `pvs` must list only a `/dev/sdb` partition, and `sda` must show no
partitions or signatures (or only ones Goobs agrees to lose). Then, with Goobs's OK:

```sh
ssh root@192.168.1.38 'pvcreate /dev/sda && vgcreate portalgun-spare /dev/sda \
  && lvcreate -l 95%FREE --thinpool data portalgun-spare \
  && pvesm add lvmthin PortalGunSpare --vgname portalgun-spare --thinpool data --content images,rootdir --nodes RicksPortalGun \
  && pvesm status | grep PortalGunSpare'
```

Undo: `pvesm remove PortalGunSpare; vgremove portalgun-spare; pvremove /dev/sda` (with no VM disks on it).

## 4. Move apps, then grow gpu and ai

Outside Tdarr's hours is not needed for apps, but n8n, the sysadmin, Nextcloud and Paperless are
offline during the copy (96 GB over 1 GbE, roughly 15 to 30 minutes).

```sh
ssh root@192.168.1.38 'pve-firewall status'   # running: VM 204's Postgres rule moves with the VM
ssh root@192.168.1.37 'qm shutdown 204 --timeout 300 && qm migrate 204 RicksPortalGun --targetstorage local-lvm'
ssh root@192.168.1.38 'qm set 204 --memory 16384 && qm start 204'
```

Then on RicksShip (each a short restart; Plex and Emby blink once):

```sh
ssh root@192.168.1.37 'qm set 203 --memory 16384 && qm shutdown 203 --timeout 300 && qm start 203'
ssh root@192.168.1.37 'qm set 205 --memory 6144 && qm shutdown 205 --timeout 300 && qm start 205'
```

Tofu: a small PR moves `apps` to `node = "RicksPortalGun"`, `datastore = "local-lvm"`,
`memory_mb = 16384`, and sets gpu 16384 and ai 6144. Merge it after the move, then re-import, because
the provider looks the VM up on its old node:

```sh
cd tofu
tofu state rm 'proxmox_virtual_environment_vm.vm["apps"]'
tofu import  'proxmox_virtual_environment_vm.vm["apps"]' RicksPortalGun/204
tofu plan    # must not say "must be replaced" for any VM; if it does, stop and do not apply
```

Check: Homepage dots green, the sysadmin chat answers, `ssh goobs@192.168.1.53 'free -m'` shows ~16 GB.

Undo: shut 204 down and `qm migrate 204 RicksShip --targetstorage RicksShipXFS` (back to 10240).

## 5. Move media

Downloads and imports stop during the copy (64 GB system disk + the 200 GB SABnzbd scratch disk).
Pause SABnzbd's queue first so nothing is mid-unpack.

```sh
ssh root@192.168.1.137 'qm shutdown 202 --timeout 300 && qm migrate 202 RicksPortalGun --targetstorage local-lvm:local-lvm,xfstest:PortalGunSpare'
ssh root@192.168.1.38  'qm set 202 --memory 12288 && qm start 202'
```

Its tofu PR moves `media` to RicksPortalGun / `local-lvm` at 12288 MB, with the scratch disk on
`PortalGunSpare` as raw
(LVM-thin has no qcow2); re-import it as in step 4 with `RicksPortalGun/202`.

Check: `systemctl is-active wait-nas docker` and `df -h /srv/usenet-incomplete` on the media VM,
resume SABnzbd, one test grab imports.

Undo: shut 202 down and migrate it back to RicksGarage (`--targetstorage local-lvm`; the scratch
disk then needs `qm disk move 202 scsi1 xfstest`).

## Afterwards

- Update [Hardware](../02-hardware.md), [Reboot a host](reboot-a-host.md) (which VMs each host runs)
  and the guest shutdown lines.
- Add RicksPortalGun to the Scrutiny collector, SMART and heartbeat playbooks, and allow it to
  apps:8087 like the other hosts.
- Later, separate OKs: Home Assistant OS VM (4 GB), Kasm (8 GB), TrueNAS 16 to 24 GB.
