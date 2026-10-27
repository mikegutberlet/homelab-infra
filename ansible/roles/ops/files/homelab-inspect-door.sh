#!/bin/bash
# Forced command for the inspect user's SSH key (the sysadmin's investigator on the apps VM).
# The whole requested line goes to homelab-inspect as one argument; that script splits it itself,
# checks every program, flag and path against its read-only lists, and runs nothing through a shell.
set -u
exec timeout -k 5 60 sudo -n /usr/local/sbin/homelab-inspect "${SSH_ORIGINAL_COMMAND:-}"
