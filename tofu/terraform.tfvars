# 192.168.1.50-.54 approved by Goobs for these VMs (2026-10-21); none answered ping beforehand.
ssh_public_keys = [
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOW8hZY715awim22lxBudezY7UPcl6Uo83oULh9J0cO4 homelab-inventory@goobss-macbook-pro",
]

vms = {
  edge = {
    vm_id     = 201
    node      = "RicksGarage"
    ip        = "192.168.1.50/24"
    cores     = 2
    memory_mb = 3072
    disk_gb   = 32
    datastore = "local-lvm"
    startup   = 3 # after TrueNAS (1) and the old Servarr VM (2)
    tags      = ["edge", "docker"]
  }
  media = {
    vm_id      = 202
    node       = "RicksGarage"
    ip         = "192.168.1.51/24"
    cores      = 4
    memory_mb  = 6144
    balloon_mb = 3072 # shares RicksGarage with TrueNAS (16 GB) and the old Servarr VM
    disk_gb    = 64
    datastore  = "local-lvm"
    startup    = 4
    tags       = ["media", "docker"]
  }
  gpu = {
    vm_id     = 203
    node      = "RicksShip"
    ip        = "192.168.1.52/24"
    cores     = 6
    memory_mb = 10240
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
    node      = "RicksShip"
    ip        = "192.168.1.53/24"
    cores     = 4
    memory_mb = 10240
    start     = false # until the old Plex VM (26 GB) is retired
    disk_gb   = 96
    datastore = "RicksShipXFS"
    tags      = ["apps", "docker"]
  }
  ai = {
    vm_id     = 205
    node      = "RicksShip"
    ip        = "192.168.1.54/24"
    cores     = 2
    memory_mb = 4096
    disk_gb   = 32
    datastore = "RicksShipXFS"
    tags      = ["ai", "docker"]
  }
}
