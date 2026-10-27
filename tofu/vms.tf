# Debian 12 cloud image, downloaded once per node. "generic", not "genericcloud":
# the genericcloud kernel has no AHCI driver, so it never sees Proxmox's cloud-init
# drive (no IP, no user), and it lacks i915 for the GPU VM.
resource "proxmox_download_file" "debian12" {
  for_each = toset(distinct([for vm in var.vms : vm.node]))

  node_name    = each.value
  content_type = "iso"
  datastore_id = "local"
  file_name    = "debian-12-generic-amd64.img"
  url          = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
  overwrite    = false
}

# Debian's images ship without qemu-guest-agent, and the provider waits for the
# agent to report an IP, so install it on first boot. Needs "snippets" enabled on
# the local storage (done 2026-10-21).
resource "proxmox_virtual_environment_file" "vendor_data" {
  for_each = toset(distinct([for vm in var.vms : vm.node]))

  node_name    = each.value
  content_type = "snippets"
  datastore_id = "local"

  source_raw {
    file_name = "homelab-vendor-data.yaml"
    data      = <<-EOF
      #cloud-config
      packages:
        - qemu-guest-agent
      runcmd:
        - systemctl enable --now qemu-guest-agent
    EOF
  }
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each = var.vms

  name      = each.key
  vm_id     = each.value.vm_id
  node_name = each.value.node
  tags      = each.value.tags

  machine       = "q35"
  bios          = "seabios"
  on_boot       = each.value.start
  started       = each.value.start
  scsi_hardware = "virtio-scsi-single"

  startup {
    order    = each.value.startup
    up_delay = 30
  }

  agent {
    enabled = true
    timeout = "10m" # fail instead of hanging if the guest never boots
  }

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory_mb
    floating  = each.value.balloon_mb
  }

  disk {
    datastore_id = each.value.datastore
    file_id      = proxmox_download_file.debian12[each.value.node].id
    interface    = "scsi0"
    size         = each.value.disk_gb
    iothread     = true
    discard      = "on"
    ssd          = true
  }

  # Scratch disk (scsi1), raw (it lives on LVM-thin, which has no qcow2). The media VM's was added
  # live by root (`qm set 202 --scsi1 xfstest:200,...`, a one-off playbook removed 2026-10-27), converted
  # to raw and moved to PortalGunSpare on 2026-10-27, and is recorded here so a plan stays clean;
  # if a plan ever wants to ADD scsi1, stop: refresh first.
  dynamic "disk" {
    for_each = each.value.scratch_gb > 0 ? [1] : []
    content {
      datastore_id = each.value.scratch_datastore
      interface    = "scsi1"
      size         = each.value.scratch_gb
      file_format  = "raw"
      iothread     = true
      discard      = "on"
      ssd          = true
      backup       = false
    }
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  # Debian cloud images boot with console=ttyS0; without a serial port the
  # first boot kernel-panics ("Attempted to kill init").
  serial_device {
    device = "socket"
  }

  # A hung guest gets reset by Proxmox instead of sitting frozen.
  watchdog {
    enabled = true
    model   = "i6300esb"
    action  = "reset"
  }

  # Iris Pro 580 for Quick Sync. The host must hand it to vfio-pci first
  # (ansible/playbooks/proxmox-igpu.yml).
  dynamic "hostpci" {
    for_each = each.value.igpu ? [1] : []
    content {
      device = "hostpci0"
      id     = "0000:00:02.0"
      pcie   = true
      rombar = true
    }
  }

  initialization {
    datastore_id        = each.value.datastore
    vendor_data_file_id = proxmox_virtual_environment_file.vendor_data[each.value.node].id

    ip_config {
      ipv4 {
        address = each.value.ip
        gateway = var.gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      username = var.admin_user
      keys     = var.ssh_public_keys
    }
  }

  operating_system {
    type = "l26"
  }

  lifecycle {
    # hostpci: the gpu VM's iGPU is attached by root (see terraform.tfvars).
    # disk[0].file_id: only used to create the disk; a VM moved to another node and re-imported
    # (operations/portalgun-moves.md) has none in state, and the plan must not rebuild it for that.
    ignore_changes = [initialization, hostpci, disk[0].file_id]
  }
}

output "vm_ips" {
  value = { for name, vm in var.vms : name => split("/", vm.ip)[0] }
}
