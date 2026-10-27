# 192.168.1.50-.54 approved by Goobs for these VMs (2026-10-21); none answered ping beforehand.
# 192.168.1.55 for the transcode VM (RicksPortalGun plan, 2026-10-27); the runbook checks it
# doesn't answer ping before the first apply (docs/wiki/operations/portalgun-moves.md).
ssh_public_keys = [
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOW8hZY715awim22lxBudezY7UPcl6Uo83oULh9J0cO4 homelab-inventory@goobss-macbook-pro",
]

vms = {
  edge = {
    vm_id     = 201
    node      = "RicksGarage"
    ip        = "192.168.1.50/24"
    cores     = 2
    memory_mb = 4096 # 3 GB left ~0.5 GB free (Authentik + tsdproxy ~1.8 GB), 2026-10-22
    disk_gb   = 32
    datastore = "local-lvm"
    startup   = 3 # after TrueNAS (1) and the old Servarr VM (2)
    tags      = ["edge", "docker"]
  }
  media = {
    vm_id      = 202
    node       = "RicksPortalGun"
    ip         = "192.168.1.51/24"
    cores      = 4
    # 8 GB fixed, no ballooning: at 6 GB with a 5 GB balloon floor it nearly ran out of memory on
    # 2026-10-22 (load >100, SSH frozen). Container peaks: SABnzbd 2.5 GB, Bazarr 1.6 GB,
    # FlareSolverr 1.5 GB, Radarr/Sonarr ~1 GB each. 12 GB since the move to RicksPortalGun
    # (2026-10-27), which has the room.
    memory_mb  = 12288
    disk_gb    = 64
    datastore  = "local-lvm"
    # SABnzbd temp disk (2026-10-22): downloads used to fill the 64 GB system disk and pause at
    # its 15 GB floor. Since 2026-10-27 it sits on PortalGunSpare, RicksPortalGun's second SSD
    # (LVM-thin, so raw), apart from the system disks on local-lvm.
    scratch_gb        = 200
    scratch_datastore = "PortalGunSpare"
    startup    = 4
    tags       = ["media", "docker"]
  }
  gpu = {
    vm_id     = 203
    node      = "RicksShip"
    ip        = "192.168.1.52/24"
    cores     = 6
    memory_mb = 16384 # was 10240; RicksShip had room once apps moved off (2026-10-27)
    disk_gb   = 96
    datastore = "RicksShipXFS"
    # The iGPU (00:02.0, vfio-pci on RicksShip since 2026-10-21) is attached by root with
    # `qm set 203 --hostpci0 0000:00:02.0,pcie=1`: the tofu@pve token can't set raw PCI devices,
    # and vms.tf ignores hostpci so tofu leaves it alone. Keep igpu = false here.
    igpu = false
    tags = ["gpu", "docker"]
  }
  apps = {
    vm_id     = 204
    # Moved to RicksPortalGun with 16 GB on 2026-10-27 (was RicksShip / RicksShipXFS / 10 GB):
    # Prometheus and Grafana are joining Nextcloud, Paperless, n8n and the sysadmin, and the
    # sysadmin now runs on the host that reboots cleanly. operations/portalgun-moves.md step 4.
    node      = "RicksPortalGun"
    ip        = "192.168.1.53/24"
    cores     = 4
    memory_mb = 16384
    disk_gb   = 96
    datastore = "local-lvm"
    tags      = ["apps", "docker"]
  }
  ai = {
    vm_id     = 205
    node      = "RicksShip"
    ip        = "192.168.1.54/24"
    cores     = 2
    memory_mb = 6144 # was 4096 (2026-10-27)
    disk_gb   = 32
    datastore = "RicksShipXFS"
    tags      = ["ai", "docker"]
  }
  # Second Tdarr node on RicksPortalGun's Iris Plus 655 (2026-10-27). The iGPU is attached by root
  # after the host binds it to vfio-pci, like the gpu VM's: `qm set 206 --hostpci0 0000:00:02.0,pcie=1`.
  # local-lvm is the 794 GB thin pool on the 1 TB system SSD (sdb); apps and media move there too.
  transcode = {
    vm_id     = 206
    node      = "RicksPortalGun"
    ip        = "192.168.1.55/24"
    cores     = 4
    memory_mb = 4096
    disk_gb   = 48 # Tdarr's /temp cache holds one movie at a time (largest ~30 GB)
    datastore = "local-lvm"
    igpu      = false
    tags      = ["transcode", "docker"]
  }
}
