# Operations and runbooks

Short, step-by-step procedures. All of them assume you are on Goobs's Mac in `~/HomeLab/homelab-infra`
with the tools installed during the build (OpenTofu, the Ansible venv at
`~/.local/share/homelab-ansible`, sops, age, gh).

| Runbook | When |
| --- | --- |
| [Deploy a change](deploy-changes.md) | Editing a Compose file, a route, a secret, an Ansible role, or VM sizing |
| [Add a service](add-a-service.md) | A new app in an existing stack |
| [Reboot a host](reboot-a-host.md) | Any Proxmox host restart (the NUCs hang on warm reboot) |
| [Power outage](power-outage.md) | Before, during and after a power cut; the UPS plan |
| [Backups and restore](backups-and-restore.md) | Checking last night's backup, restoring an app, rebuilding a VM |
| [Alerts and monitoring](alerts.md) | What an email means and what to do; adding monitors |
| [Access and credentials](access-and-credentials.md) | Where every secret lives and how to rotate it |
| [Locked out of an app](locked-out.md) | A login or API key stopped working; resets without waiting for anyone |
| [Troubleshooting](troubleshooting.md) | Known failure modes and their fixes |

## Standing rules for anyone operating the lab

1. Nothing is deleted on TrueNAS. Move to `to-delete/<date>/` with a manifest instead.
2. Host restarts are cold power-offs with a person at the button. NAS restarts need Goobs's OK in
   their own words.
3. Old and new instances of a library manager (Sonarr, Radarr) never run at the same time.
4. Change the repo, then deploy; never hand-edit a file on a VM except to debug, and then commit the
   fix. Dockge is read-only by policy.
5. Secrets go into `secrets.sops.env` through sops, or into `~/.config/homelab/` on the Mac. Never
   into chat, Git plaintext, or a VM's `/tmp`.
6. Log host-level changes with their undo step in `~/HomeLab/changes-<date>.md`.
7. Keep logs for long jobs on disk (VM `/home/goobs` or the Mac), not `/tmp`: a reboot wiped one.
