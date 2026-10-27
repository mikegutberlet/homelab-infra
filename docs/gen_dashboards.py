"""Regenerate the home-made Grafana dashboards (Home lab overview, Media stack, TrueNAS) in
stacks/apps/grafana/dashboards/Home lab/. The community ones next to them (node, containers,
Proxmox, Traefik) are grafana.com downloads and are not touched here.

Run from the repo root: python3 docs/gen_dashboards.py
"""
import json

OUT = "stacks/apps/grafana/dashboards/Home lab"
DS = {"type": "prometheus", "uid": "prometheus"}


class Board:
    def __init__(self, uid, title, description):
        self.d = {
            "uid": uid, "title": title, "description": description, "editable": False,
            "schemaVersion": 39, "time": {"from": "now-7d", "to": "now"}, "refresh": "1m",
            "tags": ["homelab"], "panels": [], "templating": {"list": []},
            "links": [{"type": "dashboards", "tags": ["homelab"], "asDropdown": True, "title": "Home lab"}],
        }
        self.y = 0
        self.id = 1

    def row(self, title):
        self.d["panels"].append({"id": self.id, "type": "row", "title": title, "collapsed": False,
                                 "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.id += 1
        self.y += 1

    def panels(self, specs, h=8):
        """specs: [(width, panel dict)] laid out left to right on one line."""
        x = 0
        for w, p in specs:
            p.update(id=self.id, datasource=DS, gridPos={"x": x, "y": self.y, "w": w, "h": h})
            self.d["panels"].append(p)
            self.id += 1
            x += w
        self.y += h

    def save(self, name):
        with open(f"{OUT}/{name}.json", "w") as f:
            json.dump(self.d, f, indent=1)
            f.write("\n")


def targets(*exprs):
    return [{"datasource": DS, "expr": e, "legendFormat": l, "refId": chr(65 + i)} for i, (e, l) in enumerate(exprs)]


def ts(title, unit, *exprs, maxv=None, stack=False, desc=""):
    defaults = {"unit": unit, "custom": {"fillOpacity": 10, "showPoints": "never",
                                         "stacking": {"mode": "normal" if stack else "none"}}}
    if maxv is not None:
        defaults.update(min=0, max=maxv)
    return {"type": "timeseries", "title": title, "description": desc, "targets": targets(*exprs),
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def stat(title, unit, expr, legend="", thresholds=None, desc=""):
    steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thresholds or [])]
    return {"type": "stat", "title": title, "description": desc, "targets": targets((expr, legend)),
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value",
                        "graphMode": "area", "textMode": "value_and_name"}}


def bars(title, unit, expr, legend, desc=""):
    return {"type": "bargauge", "title": title, "description": desc, "targets": targets((expr, legend)),
            "fieldConfig": {"defaults": {"unit": unit, "min": 0}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient",
                        "reduceOptions": {"calcs": ["lastNotNull"]}}}


CPU = '100 * (1 - avg by (vm) (rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))'
MEM = "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
ROOT = '100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'
NAS_FREE = 'max(node_filesystem_avail_bytes{mountpoint="/mnt/nfs"})'
PVE_NODE = 'id=~"node/.*"'


def overview():
    b = Board("homelab-overview", "Home lab overview",
              "Trends for the five VMs, both Proxmox hosts, the NAS mount and the containers. "
              "Alerts are not here: Kuma and the n8n digest email.")
    b.row("Right now")
    b.panels([
        (6, stat("NAS free (DeepData)", "bytes", NAS_FREE, "free",
                 desc="Free space on the NFS mount, as the VMs see it. The n8n digest warns at 4 TB.")),
        (6, stat("Scrape targets down", "short", "count(up == 0) or vector(0)", "down", [(1, "red")])),
        (6, stat("Containers running", "short", 'count(container_last_seen{name!=""})', "containers")),
        (6, stat("Busiest VM (CPU, 1 h)", "percent",
                 f"max(avg_over_time(({CPU})[1h:]))", "", [(70, "orange"), (90, "red")])),
    ], h=5)
    b.row("Proxmox hosts")
    b.panels([
        (12, ts("Host CPU", "percentunit", (f"pve_cpu_usage_ratio{{{PVE_NODE}}}", "{{id}}"), maxv=1)),
        (12, ts("Host memory used", "percentunit",
                (f"pve_memory_usage_bytes{{{PVE_NODE}}} / pve_memory_size_bytes{{{PVE_NODE}}}", "{{id}}"), maxv=1,
                desc="RicksGarage carries TrueNAS (16 GB) and is the tight one.")),
    ])
    b.row("VMs")
    b.panels([
        (8, ts("CPU busy", "percent", (CPU, "{{vm}}"), maxv=100)),
        (8, ts("Memory used", "percent", (MEM, "{{vm}}"), maxv=100)),
        (8, ts("Load (15 min) per core", "short",
               ("node_load15 / on (instance) count by (instance) (node_cpu_seconds_total{mode=\"idle\"})", "{{vm}}"),
               desc="Above 2 counts as a warning in the n8n digest.")),
    ])
    b.panels([
        (8, ts("System disk used", "percent", (ROOT, "{{vm}}"), maxv=100)),
        (8, ts("Network in", "Bps",
               ('sum by (vm) (rate(node_network_receive_bytes_total{device=~"eth.*|ens.*"}[$__rate_interval]))', "{{vm}}"))),
        (8, ts("Network out", "Bps",
               ('sum by (vm) (rate(node_network_transmit_bytes_total{device=~"eth.*|ens.*"}[$__rate_interval]))', "{{vm}}"))),
    ])
    b.panels([
        (12, ts("NAS free over time", "bytes", (NAS_FREE, "DeepData free"),
                desc="Tdarr's HEVC flow pauses below 3 TB.")),
        (12, ts("Swap used", "bytes", ("node_memory_SwapTotal_bytes - node_memory_SwapFree_bytes", "{{vm}}"))),
    ])
    b.row("Containers")
    b.panels([
        (12, bars("Top 15 by memory", "bytes",
                  'topk(15, container_memory_working_set_bytes{name!=""})', "{{vm}} / {{name}}")),
        (12, bars("Top 15 by CPU (cores, 5 min)", "short",
                  'topk(15, rate(container_cpu_usage_seconds_total{name!=""}[5m]))', "{{vm}} / {{name}}")),
    ], h=12)
    b.row("Edge")
    b.panels([
        (12, ts("Requests per app (Traefik)", "reqps",
                ("sum by (router) (rate(traefik_router_requests_total[$__rate_interval]))", "{{router}}"))),
        (12, ts("Errors and auth redirects (Traefik)", "reqps",
                ('sum by (code) (rate(traefik_router_requests_total{code=~"[345].."}[$__rate_interval]))', "{{code}}"))),
    ])
    b.save("overview")


def media():
    b = Board("homelab-media", "Media stack",
              "Library, queue and download trends from Sonarr, Radarr, Prowlarr and SABnzbd (exportarr), "
              "plus the media containers.")
    b.row("Library")
    b.panels([
        (6, stat("Series", "short", "sonarr_series_total", "series")),
        (6, stat("Missing episodes", "short", "sonarr_episode_missing_total", "missing")),
        (6, stat("Movies", "short", "radarr_movie_total", "movies")),
        (6, stat("Missing movies", "short", "radarr_movie_missing_total", "missing")),
    ], h=5)
    b.panels([
        (12, ts("Missing over time", "short", ("sonarr_episode_missing_total", "episodes"),
                ("radarr_movie_missing_total", "movies"))),
        (12, ts("Queue size", "short", ("sum(sonarr_queue_total)", "Sonarr"), ("sum(radarr_queue_total)", "Radarr"),
                desc="Items that sit here for a day get retried by the n8n sysadmin.")),
    ])
    b.row("Downloads")
    b.panels([
        (12, ts("SABnzbd speed", "Bps", ("sabnzbd_speed_bps", "speed"))),
        (12, ts("SABnzbd downloaded per hour", "bytes", ("increase(sabnzbd_downloaded_bytes[1h])", "{{server}}"),
                stack=True)),
    ])
    b.panels([
        (12, ts("Prowlarr grabs per hour", "short",
                ("sum by (indexer) (increase(prowlarr_indexer_grabs_total[1h]))", "{{indexer}}"), stack=True)),
        (12, ts("Prowlarr failed queries per hour", "short",
                ("sum by (indexer) (increase(prowlarr_indexer_failed_queries_total[1h]))", "{{indexer}}"), stack=True)),
    ])
    b.row("Containers")
    sel = 'vm=~"media|gpu", name!=""'
    b.panels([
        (12, ts("Memory (media and gpu VMs)", "bytes", (f"container_memory_working_set_bytes{{{sel}}}", "{{vm}}/{{name}}"))),
        (12, ts("CPU (cores)", "short",
                (f"rate(container_cpu_usage_seconds_total{{{sel}}}[$__rate_interval])", "{{vm}}/{{name}}"))),
    ], h=10)
    b.save("media")


def truenas():
    nas = 'nas="nas"'
    b = Board("homelab-truenas", "TrueNAS",
              "The NAS's own reporting (netdata, pushed as Graphite to graphite-exporter on the apps VM). "
              "Pool free space comes from the VMs' NFS mount; SMART is in Scrutiny.")
    b.row("Right now")
    b.panels([
        (6, stat("DeepData free", "bytes", NAS_FREE, "free")),
        (6, stat("ARC size", "bytes", f'truenas_arcstats_size{{{nas}}}', "ARC")),
        (6, stat("Memory available", "bytes", f'truenas_meminfo_available{{{nas}}}', "available")),
        (6, stat("Uptime", "s", f'truenas_system_uptime{{{nas}}}', "up")),
    ], h=5)
    b.row("System")
    b.panels([
        (8, ts("CPU per core", "percent",
               (f'truenas_cpu_usage_cpu{{{nas}, dimension=~"cpu[0-9]+"}}', "{{dimension}}"), maxv=100)),
        (8, ts("Load", "short", (f'truenas_system_load{{{nas}}}', "{{dimension}}"))),
        (8, ts("Memory", "bytes", (f'truenas_meminfo_total{{{nas}}}', "total"),
               (f'truenas_meminfo_available{{{nas}}}', "available"), (f'truenas_arcstats_size{{{nas}}}', "ARC"))),
    ])
    b.row("Disks (the five DAS drives and the boot disk)")
    b.panels([
        (8, ts("Read per disk", "KiBs", (f'truenas_disk{{{nas}, dimension="reads"}}', "{{device}}"))),
        (8, ts("Write per disk", "KiBs", (f'abs(truenas_disk{{{nas}, dimension="writes"}})', "{{device}}"))),
        (8, ts("Busy per disk", "ms", (f'truenas_disk_busy{{{nas}}}', "{{device}}"),
               desc="Milliseconds per second the disk was busy; 1000 means saturated.")),
    ])
    b.row("Network and NFS")
    b.panels([
        (12, ts("Network (ens18)", "Kbits", (f'truenas_net_ens18{{{nas}, dimension="received"}}', "received"),
                (f'abs(truenas_net_ens18{{{nas}, dimension="sent"}})', "sent"))),
        (12, ts("NFS read and write", "KiBs", (f'abs(truenas_nfsd_io{{{nas}}})', "{{dimension}}"))),
    ])
    b.save("truenas")


if __name__ == "__main__":
    overview()
    media()
    truenas()
