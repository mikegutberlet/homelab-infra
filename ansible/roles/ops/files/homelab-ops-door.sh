#!/bin/bash
# Forced command for the ops user's SSH key. Whatever n8n asks for arrives in
# SSH_ORIGINAL_COMMAND; only plain words reach homelab-ops, which is the only sudo rule.
set -u
cmd=${SSH_ORIGINAL_COMMAND:-}
# n8n's SSH node (node-ssh) prefixes every command with its working directory: "cd / ; "
# (shell-escape leaves "/" unquoted) or "cd '/' ; " in other versions. The character filter
# below would refuse either, so drop exactly that prefix and nothing else.
cmd=${cmd#"cd / ; "}
cmd=${cmd#"cd '/' ; "}
re='^[A-Za-z0-9 _./:-]*$'
[[ "$cmd" =~ $re ]] || { echo '{"result":"refused","detail":"bad characters in command"}'; exit 2; }
# shellcheck disable=SC2206
words=($cmd)
exec sudo -n /usr/local/sbin/homelab-ops "${words[@]}"
