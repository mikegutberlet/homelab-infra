terraform {
  required_version = ">= 1.8"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.117"
    }
  }
}

# Auth comes from the environment so nothing secret lives in this repo:
#   PROXMOX_VE_ENDPOINT=https://192.168.1.137:8006/
#   PROXMOX_VE_API_TOKEN='tofu@pve!homelab=<secret>'
provider "proxmox" {
  insecure = true # hosts use the self-signed Proxmox certificate

  ssh {
    agent    = true
    username = "root"
  }
}
