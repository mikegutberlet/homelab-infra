# Debian 12 cloud image, downloaded once per node.
resource "proxmox_virtual_environment_download_file" "debian12" {
  for_each = toset(distinct([for vm in var.vms : vm.node]))

  node_name    = each.value
  content_type = "iso"
  datastore_id = "local"
  file_name    = "debian-12-genericcloud-amd64.img"
  url          = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"
  overwrite    = false
}

resource "proxmox_virtual_environment_vm" "vm" {
  for_each = var.vms

  name      = each.key
  vm_id     = each.value.vm_id
  node_name = each.value.node
  tags      = each.value.tags

  machine       = "q35"
  bios          = "seabios"
  on_boot       = true
  started       = true
  scsi_hardware = "virtio-scsi-single"

  startup {
    order    = each.value.startup
    up_delay = 30
  }

  agent {
    enabled = true
  }

  cpu {
    cores = each.value.cores
    type  = "host"
  }

  memory {
    dedicated = each.value.memory_mb
  }

  disk {
    datastore_id = each.value.datastore
    file_id      = proxmox_virtual_environment_download_file.debian12[each.value.node].id
    interface    = "scsi0"
    size         = each.value.disk_gb
    iothread     = true
    discard      = "on"
    ssd          = true
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
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
    datastore_id = each.value.datastore

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
    ignore_changes = [initialization]
  }
}

output "vm_ips" {
  value = { for name, vm in var.vms : name => split("/", vm.ip)[0] }
}
