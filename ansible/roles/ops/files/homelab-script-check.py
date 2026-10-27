#!/usr/bin/python3
"""Never-list for the fix scripts the investigator proposes (ops verb run-approved).

Goobs reads every script before pressing Confirm; this is the seatbelt behind that, and n8n runs
the same rules first (checkScript in stacks/apps/n8n/sysadmin/chat_common.js; keep the two in
step, docs/test_investigator.py compares them). Reads the script on stdin; prints why it is
refused and exits 1, or exits 0. A pattern list cannot catch every disguise, which is why
anything that hides what runs (eval, base64, a downloaded script, another interpreter) is
refused outright.
"""
import re
import sys

MAX_CHARS, MAX_LINES = 3000, 25
NEVER_SERVICES = r"n8n|postgres|redis|autoheal|tailscale|immich-db|immich-redis|romm-db|authentik-db|authentik-redis|scrutiny-influx"
RULES = [
    (r"\b(reboot|poweroff|shutdown|halt|kexec)\b|\binit\s+[06]\b|\btelinit\b", "rebooting or powering off is never done by a fix"),
    (r"\bsystemctl\s+(\S+\s+)*(reboot|poweroff|halt|kexec|suspend|hibernate|isolate|rescue|emergency)\b", "rebooting or powering off is never done by a fix"),
    (r"\b(zfs|zpool)\b", "ZFS and the pool are never touched"),
    (r">\s*/mnt/nfs|/mnt/nfs.*(\brm\b|\bmv\b|\bdd\b|\btruncate\b|\bshred\b|\bunlink\b|\brmdir\b|-delete\b|>)|(\brm\b|\bmv\b|\bdd\b|\btruncate\b|\bshred\b|\bunlink\b|\brmdir\b|\bchmod\b|\bchown\b|\bchgrp\b|\brsync\b|\btee\b|\bcp\b|\bln\b|\bfind\b).*/mnt/nfs",
     "nothing under /mnt/nfs is changed or deleted"),
    (r"\bu?mount\b|\bexportfs\b", "mounts are left to the remount-nas fix"),
    (r"\beval\b|\bbase64\b|\bxxd\b|\bopenssl\s+enc\b|\bsource\b|^\s*\.\s|\b(ba|da|z|k)?sh\b|<<|\bpython[0-9.]*\b|\bperl\b|\bruby\b|\bnode\b|\bphp\b|\blua\b",
     "nothing that hides what runs (eval, base64, source, here-documents, another shell or interpreter)"),
    (r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|da|z)?sh\b|\b(curl|wget)\b.*(-o|-O|--output)\b", "no downloaded scripts or files"),
    (r"\b(wget|nc|ncat|socat|telnet|scp|sftp|ftp)\b", "no file transfers or raw sockets"),
    (r"\b(iptables|ip6tables|nft|ufw|firewall-cmd)\b|\bip\s+(link|addr|address|route)\s+(set|del|delete|add|flush)\b", "the firewall and network are not changed by a fix"),
    (r"/etc/ssh|\bsshd\b|authorized_keys|/etc/sudoers|\b(passwd|chpasswd|usermod|useradd|userdel|groupmod|visudo|chage)\b", "logins, SSH and sudo are not changed by a fix"),
    (r"\b(mkfs(\.\w+)?|fdisk|sfdisk|parted|gdisk|sgdisk|wipefs|pvcreate|pvremove|vgcreate|vgremove|lvremove|lvcreate|mdadm|blkdiscard|cryptsetup)\b|\bof=/dev/", "disks and partitions are not changed by a fix"),
    (r"\b(apt|apt-get|aptitude|dpkg|snap|pip3?|npm)\b", "no package installs or removals"),
    (r"\bdocker\s+(compose\s+(\S+\s+)*)?(down|rm|rmi|kill|system\s+prune|volume\s+(rm|prune)|network\s+(rm|prune)|image\s+prune\s+-a|builder\s+prune\s+-a)\b|\bdocker\s+(\S+\s+)*prune\s+(-\S+\s+)*(-a|--all|--volumes)\b",
     "containers, volumes and networks are not removed by a fix (docker-prune is its own fix)"),
    (r"\bdocker\s+(\S+\s+)*(stop|restart|kill|pause|rm|exec|up|start|create|update)\b[^\n]*\b(" + NEVER_SERVICES + r")\b|\b(" + NEVER_SERVICES + r")\b[^\n]*\b(stop|restart|kill|pause|down)\b",
     "n8n, the databases, autoheal and tailscale are never touched"),
    (r"\bsystemctl\s+(\S+\s+)*(stop|disable|mask|kill|restart)\s+(\S+\s+)*(docker|containerd|ssh|sshd|networking|systemd-networkd|systemd-resolved|qemu-guest-agent|homelab-[\w-]+)\b",
     "Docker, SSH, networking and the sysadmin's own services are not stopped by a fix"),
    (r"(curl|http)[^\n]*(:8265|:8266|tdarr)|tdarr[^\n]*(curl|http)", "the Tdarr queue is left alone"),
    (r"\bcrontab\b(?!\s+-l\b)|^\s*(at|batch)\b|/etc/cron|\bsystemd-run\b|\bnohup\b|\bdisown\b|\bsetsid\b|(?<![&>])&\s*($|#|;)", "nothing that keeps running after the fix (cron, at, nohup, background jobs)"),
    (r"\brm\s+(-\S*\s+)*-\S*[rR]\S*\s+(-\S+\s+)*/(\s|$|\*)|\brm\s+(-\S+\s+)*/(etc|opt|usr|var|boot|home|root|srv)/?(\s|$|\*)", "no recursive deletes of system folders"),
    (r">\s*/dev/(sd|nvme|vd|xvd)|/etc/fstab|/etc/(passwd|shadow|group|gshadow|hosts\.allow|hosts\.deny|homelab-[\w.-]+)\b|/boot/|/usr/local/sbin/", "system files and disks are not written by a fix"),
    (r"\b(chmod|chown)\s+(-\S+\s+)*\S+\s+/(\s|$)|\bchmod\s+(-\S+\s+)*[0-7]*777\b", "no wide permission changes"),
    (r"\bsysctl\s+(-\S+\s+)*-w\b|/proc/sys/|/sys/(class|block|devices)/[^\n]*>|>[^\n]*/sys/", "kernel settings are not changed by a fix"),
]
COMPILED = [(re.compile(rx, re.I | re.M), why) for rx, why in RULES]


def check(script):
    if not script.strip():
        return "the script is empty"
    if len(script) > MAX_CHARS:
        return f"the script is longer than {MAX_CHARS} characters"
    lines = script.strip("\n").split("\n")
    if len(lines) > MAX_LINES:
        return f"the script has more than {MAX_LINES} lines"
    if re.search(r"[^\x20-\x7e\t\n]", script):
        return "only plain ASCII text"
    # Whole-line comments never run, and their words ("the source of the problem") would trip the rules.
    body = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    for rx, why in COMPILED:
        if rx.search(body):
            return why
    return None


if __name__ == "__main__":
    why = check(sys.stdin.read())
    if why:
        print(why)
        sys.exit(1)
