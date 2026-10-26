# Locked out of an app

What to do when a login or an API key stops working, without waiting for anyone. Every step runs
from the Mac in `~/HomeLab/homelab-infra/ansible` (Ansible venv) or over `ssh goobs@192.168.1.5x`.
Nothing here touches `/mnt/nfs`.

## First: is it the password, or the server?

The 15-minute reporter (role `ops`) fingerprints every arr login, API key and the copies other
apps hold (agent v2, 2026-10-22). So when a login fails:

1. Check your mail. A **"login changed"**, **"API key changed"** or **"holds a stale key"** email
   means the server side moved and names the fix. **"failed web login(s)"** with "the stored login
   has NOT changed" means the server is fine and the password being typed is wrong: check the
   password-manager entry (the users are `sonarr`, `radarr`, `prowlarr`; the old VM 113 entries (that VM
   was removed on 2026-10-26) do not work on the new VMs, see change #24 of 2026-10-21).
2. No mail and no digest line: the reporter would have said so. Treat it as the wrong password.

What a reboot does: it logs every browser out. A login that "stopped working after the restart" is
usually the first typed login since the app was set up (2026-10-22 was exactly this).

## Sonarr, Radarr, Prowlarr

Their pages are only reachable through Tailscale plus Authentik (role `lanfw` drops the LAN ports),
so the app's own form login is a second gate on the same door.

| Want | Run |
| --- | --- |
| Authentik is the only login (default since 2026-10-22) | `ansible-playbook playbooks/arr-auth.yml` |
| Reset a forgotten app password | `ansible-playbook playbooks/arr-auth.yml -e method=None`, open the app through Authentik, set a new user and password when asked, then run the playbook again without `-e` (External) or leave it on Forms |
| One app only | add `-e apps=radarr` |

The playbook stops the app, keeps `config.xml.bak-<date>-auth`, rewrites `<AuthenticationMethod>`,
starts the app. API keys are untouched in every mode.

By hand (the playbook is just this):

```sh
ssh goobs@192.168.1.51 'cd /opt/stacks/media && sudo docker compose stop sonarr \
  && sudo cp /opt/appdata/sonarr/config.xml /opt/appdata/sonarr/config.xml.bak-$(date +%F)-auth \
  && sudo sed -i "s#<AuthenticationMethod>[^<]*#<AuthenticationMethod>None#" /opt/appdata/sonarr/config.xml \
  && sudo docker compose start sonarr'
```

Restore yesterday's login and key instead: [Backups and restore](backups-and-restore.md) (the
nightly backup has `config.xml` and an online copy of `<app>.db`, which holds the `Users` row).

## API keys between apps

Each arr key lives in `config.xml` and is copied into Prowlarr (Settings > Apps), Bazarr, Recyclarr
(`SONARR_API_KEY` / `RADARR_API_KEY` in `stacks/media/secrets.sops.env`), Jellyseerr and Homepage
(`HOMEPAGE_<APP>_KEY` in `stacks/apps/secrets.sops.env`). The reporter compares every copy with the
app's own key and emails "holds a stale key" when one differs. Fix: paste the key from
Settings > General of the app into the consumer, or for the sops files follow
[Deploy a change](deploy-changes.md).

## SABnzbd

Login and key are plain lines in `/opt/appdata/sabnzbd/sabnzbd.ini` (`username`, `password`,
`api_key`). Stop the container, edit (blank `username`/`password` turns the login off; the page is
behind Authentik anyway), start it. A new `api_key` must be pasted into Sonarr, Radarr, Listenarr,
Chaptarr and Homepage.

## Bazarr

`/opt/appdata/bazarr/config/config.yaml`, section `auth:`: set `type: null` with the container
stopped to drop the form login; set a new one in Settings > General afterwards.

## qBittorrent

Password in Mac `~/.config/homelab/qbittorrent-webui`. Lost: stop the container, delete the
`WebUI\Password_PBKDF2` line from `/opt/appdata/qbittorrent/qBittorrent/qBittorrent.conf`, start it,
read the temporary password from `docker logs media-qbittorrent-1`, set a new one in the UI.

## Authentik (the front door)

`ssh goobs@192.168.1.50 'cd /opt/stacks/edge && sudo docker compose exec authentik-server ak
create_recovery_key 10 akadmin'` prints a one-time link valid 10 minutes. Users and groups are in
Authentik's Postgres under `/opt/appdata/authentik/db` (nightly backup via `pg_dump`).

## Homepage, Kuma, n8n, the rest

See [Access and credentials](access-and-credentials.md) for where each lives. Kuma's login is
Goobs's own; reset with `sudo docker compose exec uptime-kuma npm run reset-password` on apps.
