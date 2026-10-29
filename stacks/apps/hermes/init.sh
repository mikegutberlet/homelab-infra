#!/bin/bash
# One-shot setup for the Hermes data folder (/opt/appdata/hermes, mounted at /opt/data), run by the
# hermes-init service before the gateway starts, as the storage user. Seeds config.yaml once (Hermes
# edits it later, e.g. /model), refreshes the homelab skill from the repo every time, and writes the
# git and ssh credentials from the container environment so nothing is pasted by hand.
set -euo pipefail
D=/opt/data
umask 077
mkdir -p "$D/skills" "$D/.ssh"
[ -f "$D/config.yaml" ] || cp /seed/config.yaml "$D/config.yaml"
rm -rf "$D/skills/homelab" && cp -r /seed/skills/homelab "$D/skills/homelab" && chmod -R u+w "$D/skills/homelab"
cat > "$D/.gitconfig" <<GIT
[user]
	name = Hermes Agent
	email = hermes@homelab.invalid
[credential]
	helper = store --file $D/.git-credentials
[init]
	defaultBranch = main
GIT
printf 'https://mikegutberlet:%s@github.com\n' "$GITHUB_TOKEN" > "$D/.git-credentials"
printf '%s' "$HERMES_INSPECT_KEY" | base64 -d > "$D/.ssh/inspect_ed25519"
cat > "$D/.ssh/config" <<SSH
# The read-only inspect door on every lab VM (ansible role ops). Usage: ssh media 'docker ps'
Host edge media gpu apps ai transcode
	User inspect
	IdentityFile $D/.ssh/inspect_ed25519
	IdentitiesOnly yes
	StrictHostKeyChecking accept-new
	ConnectTimeout 8
Host edge
	HostName 192.168.1.50
Host media
	HostName 192.168.1.51
Host gpu
	HostName 192.168.1.52
Host apps
	HostName 192.168.1.53
Host ai
	HostName 192.168.1.54
Host transcode
	HostName 192.168.1.55
SSH
echo "hermes-init: config, skill, git and ssh credentials in place"
