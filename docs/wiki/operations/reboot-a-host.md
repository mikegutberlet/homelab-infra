# Reboot a host

Both NUC6i7KY hosts **hang at the end of a software reboot**. Treat every host restart as a planned
power cycle with a person at the button. The only unplanned restarts so far were the power cut.

## Before you start

- Someone must be physically at the NUC.
- RicksGarage restarts take the NAS down, so every NAS-backed app pauses. Pick a quiet time, and get
  Goobs's OK in their own words for anything that restarts TrueNAS.
- Know what the host runs: RicksGarage = truenas 110, edge 201, media 202; RicksShip = gpu 203,
  apps 204, ai 205.

## Procedure

1. Shut the guests down cleanly, apps first, TrueNAS last (RicksGarage):
   ```sh
   ssh root@192.168.1.137 'qm shutdown 202 --timeout 300; qm shutdown 201 --timeout 300; qm shutdown 110 --timeout 300'
   ssh root@192.168.1.37  'qm shutdown 205 --timeout 300; qm shutdown 204 --timeout 300; qm shutdown 203 --timeout 300'
   ```
   `qm shutdown` goes through the guest agent (installed everywhere, including the old VMs); never use
   `qm stop` on TrueNAS.
2. Confirm nothing is running: `qm list`.
3. Power the host **off**, not reboot: `shutdown -h now`.
4. Wait until the NUC's light goes out, then press the power button once. Hold it 5 seconds and press
   again if a short press does nothing.
5. After it answers ping, check: `pvecm status` (quorate), `qm list` (guests auto-started in order),
   and on RicksGarage `zpool status DeepData` inside TrueNAS once it is up.
6. On the app VMs, `systemctl is-active wait-nas docker` and `docker ps`; then Homepage dots.

Expected timings: TrueNAS is up about 2 minutes after the host; edge and media follow at 30 s gaps;
NAS-backed containers on RicksShip start as soon as wait-nas sees the share.

## If a host does not come back

- Hold the power button 5 seconds, wait, press again.
- With a monitor attached: RicksShip's screen goes dark partway through boot because the iGPU
  belongs to the gpu VM. That is normal; judge by ping, not the screen.
- If RicksShip is down and RicksGarage was also restarted, remember `wait_for_all`: neither starts
  VMs until both have been seen once.

## Things not to do

- No `reboot`, no Proxmox "Reboot" button. (`reboot=efi` on RicksGarage did not fix the hang;
  `reboot=pci` or a BIOS update is the next experiment, only with Goobs nearby.)
- Do not enter the BIOS from a warm reboot; power off first.
- Do not restart TrueNAS without Goobs's explicit OK.

## Changes that need a host restart

iGPU/vfio changes, kernel upgrades, IOMMU or `reboot=` cmdline edits, and the BIOS "After Power
Failure" setting. Proxmox package upgrades without a kernel change do not.
