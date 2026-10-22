# Deploy a change

## Workflow

1. Branch from `main`, change files, open a PR, Goobs reviews and merges. Each PR so far (#1 to #7)
   followed this; merged branches are not reused.
2. On the Mac: `git checkout main && git pull`.
3. Deploy with the command for the layer you changed (below).
4. Verify (container healthy, Kuma green, Homepage dot green), then note anything host-level in the
   change log.

```sh
cd ~/HomeLab/homelab-infra
source ~/.local/share/homelab-ansible/bin/activate   # or call the venv's ansible-playbook directly
cd ansible
```

## Compose or stack config change (most common)

```sh
ansible-playbook playbooks/site.yml --limit media --tags stacks
```

This rsyncs `stacks/media/` to `/opt/stacks/media/` on the VM, decrypts `secrets.sops.env` to `.env`,
and runs `docker compose up -d --wait` for the services listed under `stack_services` in
`inventory/hosts.yml` (whole stack with `--remove-orphans` if the host has no list).

Gotchas:

- Compose does **not** recreate a container when only a bind-mounted config file changed. After
  editing `routes.yml`, `apps.yaml`, `homepage/services.yaml`, `recyclarr.yml`, `olivetin/config.yaml`
  or `nextcloud/nginx.conf`, restart that container: `docker compose -f /opt/stacks/<vm>/compose.yaml
  restart <service>` on the VM (as root or with sudo; the `goobs` user has sudo).
- Mount config **folders**, not single files: a single-file bind mount keeps serving the old inode
  after rsync replaces the file.
- To start a service that was held back, add it to `stack_services` in the inventory and redeploy.

## Routes (a new app name)

1. Add the app to `APPS` in `docs/gen_routes.py` (host, port, behind Authentik or not).
2. `python3 docs/gen_routes.py` from the repo root regenerates `stacks/edge/traefik/dynamic/routes.yml`
   and `stacks/edge/tsdproxy/apps.yaml`.
3. Deploy the edge stack, then restart `traefik` and `tsdproxy`. The new name gets its certificate on
   first visit (up to a minute).
4. Add the app to `stacks/apps/homepage/services.yaml` and to `MONITORS` in `docs/kuma_setup.py`,
   deploy apps, and have Goobs re-run the Kuma script (idempotent).

## Secrets

```sh
cd stacks/<vm>
sops secrets.sops.env            # edit in place; the age key is in ~/.config/sops/age/keys.txt
```

Then deploy that stack with `--tags stacks`. Secrets never appear in `git diff` as plaintext. The
example files `secrets.example.env` list every variable a stack expects. New values Goobs must type
(API keys, passwords) go through a hidden prompt into `~/.config/homelab/<name>` first, then into sops.

## OS-level change (roles base, docker, nfs, backup, lanfw, gpu)

```sh
ansible-playbook playbooks/site.yml --limit gpu --tags lanfw      # one role
ansible-playbook playbooks/site.yml --limit gpu --skip-tags stacks # everything but the apps
```

Run one VM at a time. `lanfw` re-applies the firewall immediately; `nfs` installs `wait-nas` without
restarting anything (it takes effect at the next boot); `docker` restarts Docker only if
`daemon.json` changed (containers survive thanks to `live-restore`).

## VM sizing or a new VM (OpenTofu)

```sh
cd tofu
set -a; . ~/.config/homelab/proxmox-tofu.env; set +a
tofu plan
tofu apply
```

Memory and CPU changes apply at the next VM power cycle (shut down cleanly through Proxmox; the
guest agent is installed). `initialization` (cloud-init) and `hostpci` are in `ignore_changes`: IP
changes and the iGPU attachment are done by hand. Disk size can only grow.

For a new VM: add it to `vms` in `terraform.tfvars`, apply, add it to `inventory/hosts.yml`, create
`stacks/<name>/` with a compose file and `secrets.sops.env`, run `site.yml --limit <name>
--skip-tags stacks` first, then with stacks. Remember the RAM budget (see [Hardware](../02-hardware.md)).

## Host playbooks

```sh
ansible-playbook playbooks/proxmox-scrutiny.yml       # safe, no reboot
ansible-playbook playbooks/proxmox-heartbeat.yml      # safe; needs heartbeat_url set per host
ansible-playbook playbooks/proxmox-igpu.yml           # changes boot config: follow Reboot a host
```

## Checking a deploy

```sh
ssh goobs@192.168.1.51 'sudo docker ps --format "table {{.Names}}\t{{.Status}}"'
ssh goobs@192.168.1.51 'sudo docker compose -f /opt/stacks/media/compose.yaml logs --tail 50 sonarr'
```

Then Homepage (`home.tail9af84.ts.net`) for the dots and Kuma (`status.tail9af84.ts.net`) for history.
