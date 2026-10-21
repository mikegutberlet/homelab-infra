variable "gateway" {
  description = "LAN gateway for the flat 192.168.1.0/24 network"
  type        = string
  default     = "192.168.1.1"
}

variable "dns_servers" {
  type    = list(string)
  default = ["192.168.1.1"]
}

variable "ssh_public_keys" {
  description = "Keys allowed to log in as the admin user on every VM"
  type        = list(string)
}

variable "admin_user" {
  type    = string
  default = "goobs"
}

variable "vms" {
  description = "Every app VM. Sizes come from the 2026-10-21 inventory (31 GiB per NUC)."
  type = map(object({
    vm_id      = number
    node       = string
    ip         = string # CIDR, e.g. 192.168.1.50/24
    cores      = number
    memory_mb  = number
    balloon_mb = optional(number, 0) # >0 lets Proxmox reclaim RAM down to this floor
    disk_gb    = number
    datastore  = string
    igpu       = optional(bool, false)
    startup    = optional(number, 10)
    start      = optional(bool, true) # false = created but left stopped (and not started on boot)
    tags       = list(string)
  }))
}
