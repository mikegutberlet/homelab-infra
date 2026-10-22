# Hardware

Inventoried read-only on 2026-10-21 (full report on the Mac at `~/HomeLab/inventory-phase1.md`).

## Proxmox hosts

Both hosts are the same model and are already at their maximum RAM.

| | RicksGarage | RicksShip |
| --- | --- | --- |
| Role | Storage host: carries the DAS and the TrueNAS VM, plus edge and media | Compute host: gpu, apps and ai VMs; hands its iGPU to the gpu VM |
| Model | Intel NUC6i7KY (Skull Canyon) | Intel NUC6i7KY |
| CPU | i7-6770HQ, 4 cores / 8 threads, Skylake | same |
| RAM | 31 GiB usable (32 GB installed, the NUC's maximum) | same |
| GPU | Iris Pro Graphics 580 (Quick Sync; stays with the host, i915) | Iris Pro Graphics 580, PCI 0000:00:02.0, device 8086:193b, bound to vfio-pci and passed to VM 203 |
| Network | one 1 GbE port, bridge `vmbr0` | same |
| LAN address | 192.168.1.137 | 192.168.1.37 |
| Proxmox | 8.4.21, kernel 6.8.12-43-pve (was 8.0.4) | 8.4.21, kernel 6.8.12-43-pve (was 8.1.3) |
| VM storage | `local` (ISO, snippets) and `local-lvm` | `local`, `local-lvm` and `RicksShipXFS` (directory storage; the app VMs' disks live here) |
| IOMMU | on (`intel_iommu=on iommu=pt`), iGPU alone in group 0 | on, iGPU alone in group 0 |
| Boot | UEFI, GRUB (`grub-pc` package installed although the host boots UEFI; `grub-efi-amd64` someday) | same |
| Kernel cmdline extras | `reboot=efi` added 2026-10-21 (did not fix the warm-reboot hang) | none |
| Measured CPU package draw | about 25 W | about 8 W |

### Cluster

The two hosts form one Proxmox cluster. On 2026-10-21 it still expected three votes because a
third node, *PlanetC137* (192.168.1.237), had been offline for about 609 days. It was removed with
`pvecm delnode`, and the cluster now runs in `two_node` mode (expected votes 2, flags
`2Node Quorate WaitForAll`). Either host can be down without the other losing quorum, with one
catch: after a **cold start of both hosts**, `wait_for_all` means both must be seen once before
either starts its VMs.

There is no Proxmox HA. The storage-side VMs cannot move anyway because TrueNAS is tied to the DAS
on RicksGarage, and a third vote (QDevice) would be needed for failover. Self-healing happens
inside the VMs instead (see [Architecture](03-architecture.md#self-healing-layers)).

### The warm-reboot hang (important)

Both NUCs hang at the end of a software reboot (`reboot`, or Proxmox's reboot button). Every host
restart therefore needs a person at the power button: shut the guests down cleanly, power the host
off, and press the button. `reboot=efi` was tried on RicksGarage and did not help; `reboot=pci` or a
BIOS update are the next things to try, with Goobs nearby. Runbook:
[Reboot a host](operations/reboot-a-host.md).

### BIOS auto power-on (pending)

After the 2026-10-21 power cut both NUCs stayed off until Goobs pressed the buttons. The fix is the
BIOS setting *Power > Secondary Power Settings > After Power Failure: Power On* on each NUC. As of
2026-10-22 this has **not** been confirmed done. Because of the warm-reboot hang, enter the BIOS
from a cold power-on, not from a reboot.

## Storage hardware

| Item | Detail |
| --- | --- |
| Enclosure | USB direct-attached storage (DAS) on RicksGarage, five bays |
| Disks | 5 x 20 TB Seagate Exos |
| How TrueNAS sees them | As five **virtual disks** attached to VM 110 (not USB or PCI passthrough). TrueNAS therefore cannot read SMART data; the Proxmox host can, which is why the Scrutiny collector runs on the hosts |
| Pool | `DeepData`, RAIDZ1 (one disk of redundancy across about 100 TB raw) |
| Capacity | 54.2 TB used, 18.3 TB free at the inventory; 74% after the outage check on 2026-10-22 |
| Health | ONLINE, no errors. A weekly scrub was running during the build and resumed after the outage |
| Disk to watch | Serial `ZVTAM9MF` (20 TB) shows a few USB command timeouts in Scrutiny. No bad sectors, SMART passes; common with USB enclosures |
| Other disks | Scrutiny shows nine disks in total: the five Exos plus the hosts' own system disks (exact models not recorded) |
| Old enclosure | VM 118's config still points at a disconnected Sabrent USB enclosure; left alone |

The TrueNAS VM had 32 GB of RAM assigned on a 31 GiB host, which made RicksGarage swap and slowed
the scrub to 33 MB/s. It was cut to a fixed 16 GB (ballooning off) on 2026-10-21; the scrub then ran
at 230 to 308 MB/s. The pre-change VM config is saved on the Mac
(`~/HomeLab/qm110-before-ramcut-20261021.conf`).

## Network hardware

| Item | What is known |
| --- | --- |
| Router / gateway | 192.168.1.1, also the DNS server handed to the VMs. Model `(unverified)`. Does not do VLANs. Has one port forward: TCP 6996 to 192.168.1.52:32400 (Plex) |
| Switch | A flat, unmanaged switch; everything is on one 192.168.1.0/24 segment |
| DHCP range | Not recorded. 192.168.1.50 to .54 were checked free before use `(router range unverified)` |
| Internet | Residential connection; provider and speed not recorded |
| Tailscale | Tailnet `tail9af84.ts.net`; the edge VM advertises 192.168.1.0/24 as subnet router `lab-router`; MagicDNS and HTTPS certificates enabled |

## Client devices

Known from Plex history (2,425 plays) and the Tdarr survey:

- Three Roku TVs, named *Lanikai*, *Makua* and *The Strand* in Plex. They direct-play 8-bit and
  10-bit HEVC; none plays DTS; Lanikai transcodes every subtitled play (a setting in its Plex app).
- A Google Pixel phone, which direct-plays HEVC including 10-bit.
- Goobs's Intel MacBook Pro ("goobss-MBP"), the machine Remote Control sessions run on. It holds
  the repo clone, the age key, the Proxmox token and the Ansible/OpenTofu tools.

## Not in the lab (yet)

- **GTX 1660 Ti eGPU**: owned, not connected, not in the current plan. When it arrives it attaches
  to RicksShip over Thunderbolt and would go to the ai VM for local Ollama models.
- **UPS**: none. A 1500 VA / 900 W line-interactive unit with USB (CyberPower CP1500PFCLCD or APC
  BR1500MS2 class) is recommended; see [Power outage](operations/power-outage.md).
- **Cameras**: none, so Frigate is on hold.
- **Spare machine** for Kasm or a cluster QDevice: none.

## Power budget

Estimated from specs, not metered:

| Load | Steady | Note |
| --- | --- | --- |
| Two NUC6i7KY | about 60 W | 65 W adapters; 15 to 45 W each in practice |
| DAS with five Exos | about 45 W | up to 120 W for a few seconds at spin-up |
| Router, modem, switch | about 25 W | must be on the UPS too, or alerts and Tailscale die with the power |
| Total | 130 to 150 W | about 250 W peak at power-up |
