# Add a service

Checklist for a new app, using the conventions every existing service follows.

1. **Pick the VM** by trust and resources: downloads and library tools on media; anything needing
   `/dev/dri` on gpu; personal data, databases and ops on apps; LLM things on ai. Check free RAM on
   the VM and the host first (`free -h` on the VM, Proxmox summary for the host); RicksGarage is
   tight.
2. **Compose entry** in `stacks/<vm>/compose.yaml`:
   - `<<: *common` (restart policy + autoheal label), `PUID/PGID/TZ` from `*lsio-env` or explicit
     `user: ${PUID}:${PGID}`.
   - A **healthcheck** (HTTP endpoint or command) so autoheal and `--wait` mean something.
   - Config under `/opt/appdata/<name>`; if the container writes there as uid 3000, add the name to
     `appdata_owned_dirs` for that host in `inventory/hosts.yml`; if the image insists on its own uid,
     use `appdata_dir_owners: {name: uid}`.
   - NAS paths under `/mnt/nfs/...`, read-only unless the app must write; new NAS folders are created
     by hand, add-only, owned 3000:3000, with Goobs's OK, and noted in the change log.
   - A published port on the VM's LAN IP (Traefik proxies to it). Shared databases: use the apps VM's
     Postgres (`postgres/init.sh` creates a database and user from a new password variable) unless the
     app needs a pinned extension.
   - Pin a major version tag; comment any quirk the way the existing files do.
3. **Secrets**: add variables to `secrets.example.env` and, with `sops`, to `secrets.sops.env`.
4. **Start list**: add the service to `stack_services` for that host (or remove the list to run the
   whole stack).
5. **Route**: add it to `APPS` in `docs/gen_routes.py` (behind Authentik unless it has a mobile app
   with its own login), run the script, deploy edge, restart traefik and tsdproxy.
6. **Firewall**: nothing to do for the default (lab VMs only). Open to the LAN only with a reason, via
   `lan_open_ports`; edge-only via `edge_only_ports`.
7. **Observability**: Homepage entry (`stacks/apps/homepage/services.yaml`, with a `siteMonitor`),
   Kuma monitor (`docs/kuma_setup.py`, critical only if the household notices within minutes), and
   check the backup script covers its data (SQLite/Postgres/MariaDB are automatic; large regenerable
   data should be added to the tar excludes).
8. **Deploy** per [Deploy a change](deploy-changes.md), verify, and add the app to the wiki's
   service page and the overview table.
9. **First-run**: claim the admin account before leaving it, since its setup page is reachable from
   the lab VMs and, through Traefik, from the tailnet.
