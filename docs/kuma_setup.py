"""Create Goobs's Uptime Kuma monitors and notifications. Goobs runs this; it asks for the
Kuma login and the Gmail app password itself and stores nothing.

    ~/.local/share/homelab-ansible/bin/python docs/kuma_setup.py

Kuma isn't reachable from the LAN (lanfw) and its tailnet name sits behind Authentik, so this
opens an SSH tunnel to the apps VM and talks to Kuma on localhost. Existing monitors (same
name) are left alone, so it is safe to run again.
"""
import getpass
import socket
import subprocess
import sys
import time

from uptime_kuma_api import MonitorType, NotificationType, UptimeKumaApi

APPS_VM = "goobs@192.168.1.53"
LOCAL_PORT = 13002
MAIL_FROM = "filingbin69@gmail.com"
MAIL_TO = "mikegutberlet@gmail.com"
DIGEST_WEBHOOK = "http://n8n:5678/webhook/homelab/kuma"  # Kuma and n8n share a network

# (name, url, critical, accepted status codes). Critical = immediate email; others = daily digest.
MONITORS = [
    ("Plex", "http://192.168.1.52:32400/identity", True, None),
    ("Emby", "http://192.168.1.52:8096/emby/System/Ping", True, None),
    ("Sonarr", "http://192.168.1.51:8989/ping", True, None),
    ("Radarr", "http://192.168.1.51:7878/ping", True, None),
    ("Prowlarr", "http://192.168.1.51:9696/ping", True, None),
    ("SABnzbd", "http://192.168.1.51:8080/", True, ["200-299", "300-399"]),
    # qBittorrent sits behind gluetun; any HTTP answer (even 403 without login) means both are up.
    ("qBittorrent (via PIA)", "http://192.168.1.51:8085/api/v2/app/version", True, ["200-299", "300-399", "400-499"]),
    ("Requests (Jellyseerr)", "http://192.168.1.51:5055/api/v1/status", True, None),
    ("Bazarr", "http://192.168.1.51:6767/", False, ["200-299", "300-399"]),
    ("Audiobookshelf", "http://192.168.1.51:13378/healthcheck", False, None),
    ("Storyteller", "http://192.168.1.51:8001/", False, ["200-299", "300-399"]),
    ("RomM", "http://192.168.1.51:8095/", False, ["200-299", "300-399"]),
    ("MeTube", "http://192.168.1.51:8081/", False, None),
    ("Tautulli", "http://192.168.1.53:8181/status", False, None),
    ("Nextcloud", "http://192.168.1.53:8090/status.php", False, None),
    ("Paperless", "http://192.168.1.53:8000/", False, ["200-299", "300-399"]),
    ("Stirling PDF", "http://192.168.1.53:8070/", False, ["200-299", "300-399"]),
    ("n8n", "http://192.168.1.53:5678/healthz", False, None),
    ("Huginn", "http://192.168.1.53:3000/users/sign_in", False, None),
    ("Homepage", "http://192.168.1.53:3001/", False, ["200-299", "300-399", "400-499"]),
    ("Scrutiny", "http://192.168.1.53:8087/api/health", False, None),
    ("Dockge", "http://192.168.1.53:5001/", False, ["200-299", "300-399"]),
    ("Edge (Traefik, TCP)", "192.168.1.50:80", False, None),  # Kuma isn't on the tailnet
    ("Piper (TCP)", "192.168.1.54:10200", False, None),
    ("TrueNAS", "https://192.168.1.209/", False, ["200-299", "300-399"]),
    ("Proxmox RicksGarage", "https://192.168.1.137:8006/", False, None),
    ("Proxmox RicksShip", "https://192.168.1.37:8006/", False, None),
]


def open_tunnel():
    proc = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                             "-L", f"{LOCAL_PORT}:localhost:3002", APPS_VM])
    for _ in range(40):
        try:
            socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.25)
    proc.terminate()
    sys.exit("Could not open the SSH tunnel to the apps VM.")


def notification_id(api, name, **settings):
    for n in api.get_notifications():
        if n["name"] == name:
            return n["id"]
    return api.add_notification(name=name, isDefault=False, applyExisting=False, **settings)["id"]


def kuma2_compat(api):
    """uptime-kuma-api 1.2 targets Kuma 1.x; Kuma 2 refuses new monitors with conditions = NULL."""
    if getattr(api, "_kuma2_compat", False):
        return
    api._kuma2_compat = True
    orig = api._call

    def _call(event, data=None):
        if event in ("add", "editMonitor") and isinstance(data, dict) and not data.get("conditions"):
            data["conditions"] = "[]"
        return orig(event, data)

    api._call = _call


def existing_monitor_names(api):
    """Kuma 2 only sends incremental monitor updates, so this library's monitor list stays empty.
    get_monitor(id) still works: walk the ids until 25 in a row are missing."""
    names, misses, i = set(), 0, 1
    while misses < 25 and i < 2000:
        try:
            names.add(api.get_monitor(i)["name"])
            misses = 0
        except Exception:
            misses += 1
        i += 1
    return names


def configure(api, gmail):
    """Notifications + monitors. Idempotent: existing ones (same name) are left alone."""
    kuma2_compat(api)
    digest = notification_id(api, "n8n daily digest", type=NotificationType.WEBHOOK,
                             webhookURL=DIGEST_WEBHOOK, webhookContentType="json")
    urgent = None
    if gmail:
        urgent = notification_id(
            api, "Email now (critical)", type=NotificationType.SMTP,
            smtpHost="smtp.gmail.com", smtpPort=465, smtpSecure=True,
            smtpUsername=MAIL_FROM, smtpPassword=gmail,
            smtpFrom=f"Homelab <{MAIL_FROM}>", smtpTo=MAIL_TO)
    existing = existing_monitor_names(api)
    added = 0
    for name, url, critical, codes in MONITORS:
        if name in existing:
            print(f"  exists   {name}")
            continue
        notify = [urgent] if (critical and urgent) else [digest]
        kw = dict(name=name, interval=60, maxretries=2, notificationIDList=notify)
        if url.startswith("http"):
            kw.update(type=MonitorType.HTTP, url=url, ignoreTls=True)
            if codes:
                kw["accepted_statuscodes"] = codes
        else:
            host, port = url.rsplit(":", 1)
            kw.update(type=MonitorType.PORT, hostname=host, port=int(port))
        api.add_monitor(**kw)
        added += 1
        print(f"  added    {name}{'  (critical: email now)' if critical and urgent else ''}")
    return added, urgent


def main():
    user = input("Uptime Kuma username: ").strip()
    password = getpass.getpass("Uptime Kuma password: ")
    gmail = getpass.getpass(f"Gmail app password for {MAIL_FROM} (Enter to skip email alerts): ")
    tunnel = open_tunnel()
    try:
        with UptimeKumaApi(f"http://127.0.0.1:{LOCAL_PORT}", timeout=30) as api:
            api.login(user, password)
            added, urgent = configure(api, gmail)
            print(f"{added} monitor(s) added.")
            if urgent:
                api.test_notification(name="Email now (critical)", type=NotificationType.SMTP, smtpHost="smtp.gmail.com", smtpPort=465,
                                      smtpSecure=True, smtpUsername=MAIL_FROM, smtpPassword=gmail,
                                      smtpFrom=f"Homelab <{MAIL_FROM}>", smtpTo=MAIL_TO)
                print(f"Sent a test email to {MAIL_TO}.")
    except Exception as e:
        sys.exit(f"Stopped: {e}")
    finally:
        tunnel.terminate()
    print("Done. Safe to run again: existing monitors and notifications are skipped.")


if __name__ == "__main__":
    main()
