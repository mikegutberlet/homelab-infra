# Power outage

Power cuts happen "from time to time" (Goobs). The first one during the lab's life was at about
18:10 Pacific on 2026-10-21, mid-way through the Radarr relink; nothing was lost. The plan doc is
"Power outage plan" in the project; this page is the operational summary.

## What happens today (no UPS)

1. Everything stops hard. ZFS and the app databases are built for this; the first outage caused no
   damage (Scrutiny crashed once at boot and recovered).
2. When power returns the NUCs **stay off** until the BIOS setting below is made or someone presses
   both buttons.
3. The hosts boot; once two of the three are up the cluster is quorate; RicksGarage starts TrueNAS (120 s),
   then edge and media; RicksShip starts gpu, apps and ai.
4. On media, gpu and apps, `wait-nas.service` holds Docker until `/mnt/nfs` is a real, non-empty
   mount, so no app ever sees an empty library (the hazard found after the first outage).
5. Containers start with healthchecks; Kuma, Homepage and the Tailscale router come back; downloads,
   Tdarr queues and the Arr apps resume on their own.

While the lab is dark, no email can be sent from inside it. The **heartbeat** from both hosts to
healthchecks.io is the only thing that can tell Goobs, and it is not active until the two check URLs
are entered in `ansible/inventory/hosts.yml` and `proxmox-heartbeat.yml` is run.

## Protections already in place

| Protection | Where |
| --- | --- |
| Docker waits for the NAS, no timeout | role `nfs`, `wait-nas.service` + `docker.service.d/wait-nas.conf` on media, gpu, apps (deployed 2026-10-22 01:35 UTC) |
| Plex "Empty trash automatically after every scan" off | Plex settings (was ON; turned off 2026-10-22) |
| Radarr / Sonarr "Unmonitor deleted" off | Already off, confirmed |
| TrueNAS first on RicksGarage | VM 110 `onboot=1`, order 1, 120 s |
| QEMU watchdog on every VM | tofu `watchdog` block + `watchdog` daemon from role base |
| Heartbeat playbook ready | `playbooks/proxmox-heartbeat.yml` (URLs pending) |

## After an outage: checklist

1. Press both power buttons if the BIOS setting is not done. Wait about 5 minutes.
2. Hosts: `pvecm status` quorate; `qm list` shows 110, 201 to 205 running and 113/117 stopped.
3. TrueNAS: pool `DeepData` ONLINE, no errors; scrub resumed if one was running; NFS answering.
4. Each of .51, .52, .53: `findmnt /mnt/nfs` is nfs4 and `ls /mnt/nfs` is not empty;
   `systemctl is-active wait-nas docker`; `docker ps` shows everything up.
5. Plex library count unchanged (3,368 movies at last check); Radarr's "movie file missing" count
   not suddenly in the thousands. Plex Settings > Remote Access is still on (it switched itself off
   in the 2026-10-21 outage; the sysadmin now turns it back on within 15 minutes).
6. Kuma: all monitors green; Scrutiny up (restart it if it crashed on a late InfluxDB).
7. Tailscale admin: `lab-router` online and the names reachable.
8. Any long-running job (Tdarr, relink, moves) that was interrupted: check its on-disk log and
   manifest before resuming. A manifest entry must exist in exactly one place, source or destination.
9. If a disk enumerated late and the pool is DEGRADED, do not scrub or resilver blindly; report it.

## What needs Goobs

- [ ] BIOS on both NUCs: F2 at power-on, Power > Secondary Power Settings > After Power Failure: **Power On**.
      Do it from a cold power-on (guests shut down cleanly first).
- [ ] Free healthchecks.io account and a read-write API key; `python3 docs/heartbeat_setup.py` creates
      the four checks (three hosts and the apps VM; period 5 min, grace 10 min) and writes the ping URLs
      into `hosts.yml` (`heartbeat_url`); then run `playbooks/proxmox-heartbeat.yml`.
- [ ] Buy a UPS. 1500 VA / 900 W line-interactive with USB (CyberPower CP1500PFCLCD or APC BR1500MS2
      class, about $200 to $250) gives roughly 30 minutes at the lab's 150 W; 1000 VA gives about 15.
- [ ] Put the router, modem and switch on the UPS as well.

## The UPS plan (when it arrives)

1. UPS USB cable to **RicksGarage** (it carries TrueNAS and the DAS). RicksGarage runs the NUT server.
2. RicksShip runs a NUT client; TrueNAS uses its built-in UPS service in slave mode pointing at
   RicksGarage.
3. Policy: after 2 minutes on battery, or at 50% charge, shut down. Order: ai, apps, gpu, media,
   edge, then TrueNAS, then the hosts. Proxmox stops each VM through the guest agent.
4. NUT tells the UPS to cut its output once everything is off, so the NUCs see a real power-on edge
   when utility power returns (needs the BIOS setting).
5. One supervised test with Goobs at the power buttons, because of the warm-reboot quirk.

The NUT configuration is not in the repo yet; it belongs in a new Ansible playbook for the `proxmox`
group plus a TrueNAS UI step, written once the model is known.
