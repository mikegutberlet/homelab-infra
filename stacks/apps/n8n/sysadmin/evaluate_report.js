// Evaluate report: one VM's homelab-agent report in, the list of issues it raises out.
// Reads and updates the per-host history in static data (previous report, restart samples,
// hourly trend points). Rules live here; the decision about emails and fixes is in Decide.
const sd = $getWorkflowStaticData('global');
sd.last = sd.last || {}; sd.rs = sd.rs || {}; sd.trends = sd.trends || {}; sd.tdarr = sd.tdarr || {};
const r = $input.first().json.body || $input.first().json;
const host = r.host;
if (!host || !r.system) return [{ json: { host: 'unknown', issues: [], ignored: true } }];
const prev = sd.last[host] || null;
const issues = [];
const add = (...a) => issues.push(issue(host, ...a));
const S = SETTINGS;
const sys = r.system || {};

// ---- system ----
if (sys.mem && sys.mem.used_pct >= S.MEM_CRIT) add('mem', 'critical', `${host}: memory ${sys.mem.used_pct}% used`, `${gb(sys.mem.available)} available of ${gb(sys.mem.total)}`);
else if (sys.mem && sys.mem.used_pct >= S.MEM_WARN) add('mem', 'warning', `${host}: memory ${sys.mem.used_pct}% used`, `${gb(sys.mem.available)} available`);
if (sys.swap && sys.swap.used > S.SWAP_WARN_BYTES) add('swap', 'warning', `${host}: ${gb(sys.swap.used)} of swap in use`, 'The VM is short of RAM; check its balloon floor in Proxmox');
if (sys.load && sys.cores && sys.load[2] > S.LOAD_FACTOR * sys.cores) add('load', 'warning', `${host}: 15-min load ${sys.load[2]} on ${sys.cores} cores`, 'Sustained for at least 15 minutes');
if (sys.uptime_s != null && sys.uptime_s < 1200) add('rebooted', 'warning', `${host} restarted ${Math.round(sys.uptime_s / 60)} min ago`, 'Unplanned unless you did it; check the Proxmox task log');
if (sys.reboot_required) add('reboot-required', 'info', `${host}: a kernel or libc update is waiting for a reboot`, 'Power off cleanly from Proxmox when convenient (NUC hosts: never a host reboot)');
if (sys.pending_updates > 0) add('updates', 'info', `${host}: ${sys.pending_updates} package update(s) pending`, 'unattended-upgrades installs security fixes; the rest wait for apt upgrade');
if (sys.clock_synced === false) add('clock', 'warning', `${host}: clock not NTP-synced`, 'Certificates and Tailscale depend on correct time');

// ---- disks and mounts ----
for (const d of r.disks || []) {
  if (d.used_pct >= S.DISK_CRIT) add(`disk:${d.mount}`, 'critical', `${host}: ${d.mount} is ${d.used_pct}% full`, `${gb(d.avail)} left; app databases live here`);
  else if (d.used_pct >= S.DISK_WARN) add(`disk:${d.mount}`, 'warning', `${host}: ${d.mount} is ${d.used_pct}% full`, `${gb(d.avail)} left`);
}
const m = r.mounts || {};
if (m.nfs && !(m.nfs.mounted && m.nfs.nonempty)) {
  if (m.nas_tcp_2049) add('nfs', 'critical', `${host}: /mnt/nfs is ${m.nfs.mounted ? 'mounted but empty' : 'not mounted'} while the NAS is up`, 'Apps are running against an empty library path', { verb: 'remount-nas', target: 'nfs' });
  else add('nfs', 'critical', `${host}: /mnt/nfs is not available and TrueNAS does not answer on 2049`, 'Waiting for the NAS; nothing to fix from here');
}
if (m.backups_mounted === false && m.nas_tcp_2049) add('backups-mount', 'warning', `${host}: /mnt/backups is not mounted`, 'Tonight\'s backup will fail unless the automount comes back');

// ---- backups ----
const b = r.backup || {};
if (b.last_success) {
  const h = (now / 1000 - b.last_success) / 3600;
  if (h > S.BACKUP_CRIT_H) add('backup-age', 'critical', `${host}: last good backup ${Math.round(h)} h ago`, `Newest folder ${b.newest || 'none'}; check journalctl -u homelab-backup`);
  else if (h > S.BACKUP_WARN_H) add('backup-age', 'warning', `${host}: last good backup ${Math.round(h)} h ago`, 'The 03:30 timer may have been missed');
} else if (prev) add('backup-none', 'warning', `${host}: no successful backup in the journal`, 'Expected after a fresh VM or a journal rotation; otherwise run homelab-backup by hand');
if (b.last_rc && b.last_rc !== 0) add('backup-rc', 'warning', `${host}: the last backup run exited ${b.last_rc}`, 'Details are in the digest\'s Backups section and journalctl -u homelab-backup');
if (b.newest_bytes && b.previous_bytes && b.newest_bytes < b.previous_bytes / 2) add('backup-size', 'warning', `${host}: newest backup is ${gb(b.newest_bytes)}, previous was ${gb(b.previous_bytes)}`, 'A backup that halved usually skipped something');

// ---- containers ----
const dk = r.docker || {};
sd.rs[host] = sd.rs[host] || {};
const prevHealth = {};
for (const c of (prev && prev.docker && prev.docker.containers) || []) prevHealth[c.name] = c.health;
let restartsTotal = 0;
for (const c of dk.containers || []) {
  restartsTotal += c.restarts || 0;
  const svc = c.service || c.name;
  const canFix = !S.NEVER_RESTART.includes(svc);
  // Restart samples over the last 70 minutes.
  const samples = (sd.rs[host][c.name] || []).filter((s) => now - s.t < 70 * 60e3);
  samples.push({ t: now, r: c.restarts || 0 });
  sd.rs[host][c.name] = samples;
  const delta = samples.length > 1 ? (c.restarts || 0) - samples[0].r : 0;
  if (delta >= S.RESTARTS_CRIT) add(`crashloop:${c.name}`, 'critical', `${host}: ${c.name} restarted ${delta} times in the last hour`, `State ${c.state}, exit code ${c.exit_code}; stopping beats hammering the NAS`, canFix ? { verb: 'stop', target: svc, escalate: 'restart' } : null);
  else if (delta >= S.RESTARTS_WARN) add(`restarts:${c.name}`, 'warning', `${host}: ${c.name} restarted ${delta} times in the last hour`, `State ${c.state}, health ${c.health}`);
  if (c.health === 'unhealthy') {
    if (prevHealth[c.name] === 'unhealthy') add(`unhealthy:${c.name}`, 'critical', `${host}: ${c.name} unhealthy for 30 minutes`, 'autoheal has not brought it back', canFix ? { verb: 'restart', target: svc } : null);
    else add(`unhealthy:${c.name}`, 'warning', `${host}: ${c.name} is unhealthy`, 'autoheal gets one more interval');
  }
}
for (const missing of dk.missing || []) {
  const svc = missing.split('/')[1];
  const c = (dk.containers || []).find((x) => x.service === svc);
  const state = c ? `${c.state} (exit ${c.exit_code})` : 'no container';
  add(`missing:${svc}`, 'critical', `${host}: ${svc} should be running but is ${state}`, 'Expected by stack_services / the compose file', S.NEVER_RESTART.includes(svc) ? null : { verb: 'restart', target: svc });
}
if (dk.reclaimable_bytes > S.RECLAIM_WARN_GB * GB) add('docker-space', 'warning', `${host}: ${gb(dk.reclaimable_bytes)} of dangling images and build cache`, 'docker image prune (dangling only) frees it', { verb: 'docker-prune', target: 'docker' });

// ---- media apps ----
const md = r.media || {};
for (const app of ['sonarr', 'radarr']) {
  const a = md[app]; if (!a) continue;
  for (const hc of a.health || []) if (hc.type === 'error') add(`${app}-health:${hc.source}`, 'warning', `${app}: ${hc.message}`, hc.source);
  const stuck = a.stuck || [];
  if (stuck.length > S.QUEUE_CRIT) add(`${app}-queue`, 'critical', `${app}: ${stuck.length} queue items stuck or failed`, stuck.slice(0, 5).map((q) => q.title).join('; '));
  for (const q of stuck) {
    const msg = (q.messages || []).join(' | ');
    const retryable = /no files found|not found|missing|does not exist|was not found/i.test(msg);
    if ((q.tdstate === 'importBlocked' || q.tdstate === 'importPending' || q.status === 'failed' || q.tds === 'error') && (q.age_h || 0) >= S.STUCK_RETRY_H)
      add(`${app}-stuck:${q.id}`, 'warning', `${app}: "${q.title}" ${q.tdstate || q.status} for ${q.age_h} h`, msg || 'no message', retryable ? { verb: 'arr-retry', target: `${app} ${q.id}`, app, id: q.id } : null);
    else if (q.tds === 'warning' && (q.age_h || 0) >= S.STUCK_RETRY_H)
      add(`${app}-stuck:${q.id}`, 'warning', `${app}: "${q.title}" needs a decision (${q.age_h} h)`, msg || 'no message');
    else if (q.tdstate === 'downloading' && (q.age_h || 0) >= S.STUCK_BLOCKLIST_H && q.size && q.sizeleft === q.size)
      add(`${app}-dead:${q.id}`, 'warning', `${app}: "${q.title}" has downloaded nothing in ${q.age_h} h`, `${q.protocol}; blocklist and search again`, S.BLOCKLIST_ENABLED ? { verb: 'arr-blocklist', target: `${app} ${q.id}`, app, id: q.id } : null);
  }
}
if (md.prowlarr) {
  for (const hc of md.prowlarr.health || []) if (hc.type === 'error') add(`prowlarr-health:${hc.source}`, 'warning', `prowlarr: ${hc.message}`, hc.source);
  for (const ix of md.prowlarr.stuck || []) {
    const till = ix.disabled_till ? Date.parse(ix.disabled_till) : 0;
    const major = /gingadaddy|nzbgeek|nzbplanet/i.test(ix.name);
    add(`prowlarr-indexer:${ix.indexer_id}`, major ? 'critical' : 'warning', `prowlarr: indexer ${ix.name} disabled by backoff`, `until ${fmtT(till)}; last failure ${ix.failures ? fmtT(Date.parse(ix.failures)) : '?'}`, till && till < now ? { verb: 'prowlarr-test', target: String(ix.indexer_id) } : null);
  }
}
if (md.sabnzbd) {
  const s = md.sabnzbd;
  if (s.diskspace_gb != null && s.diskspace_gb < S.SAB_DISK_CRIT_GB) add('sab-disk', 'critical', `SABnzbd sees only ${s.diskspace_gb} GB free`, 'Downloads will pause; check the NAS and the sabnzb folder');
  if (s.paused && s.queue_count > 0) add('sab-paused', 'warning', `SABnzbd is paused with ${s.queue_count} item(s) queued`, s.pause_int && s.pause_int !== '0' ? `timed pause, ${s.pause_int} min left` : 'manual or disk pause');
  if ((s.failed_24h || []).length) add('sab-failed', 'warning', `SABnzbd: ${s.failed_24h.length} failed download(s) in 24 h`, s.failed_24h.slice(0, 5).map((f) => `${f.name}: ${f.fail_message || ''}`).join('; '));
}
if (md.qbittorrent) {
  const q = md.qbittorrent;
  if (q.exit_ip && q.vpn_ok === false) add('qb-vpn', 'critical', 'qBittorrent is NOT behind the VPN', `exit IP ${q.exit_ip} equals the home IP`, { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
  else if (!q.exit_ip) add('qb-vpn', 'warning', 'gluetun has no internet through PIA', 'tunnel down or reconnecting', { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
  if (q.exit_ip && q.vpn_ok && q.forwarded_port == null) add('qb-port', 'warning', 'PIA forwarded port missing', 'Seeding and torrent speed suffer', { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
  if ((q.errored || []).length) add('qb-errored', 'warning', `qBittorrent: ${q.errored.length} torrent(s) in error`, q.errored.slice(0, 5).map((t) => `${t.name} (${t.state})`).join('; '));
  if ((q.stalled_3d || []).length) add('qb-stalled', 'info', `qBittorrent: ${q.stalled_3d.length} torrent(s) stalled over 3 days`, q.stalled_3d.slice(0, 5).map((t) => t.name).join('; '));
}
if (md.tdarr) {
  const t = md.tdarr; const pt = sd.tdarr[host] || {};
  if (t.errored != null && pt.errored != null && t.errored > pt.errored) add('tdarr-errors', 'warning', `Tdarr: ${t.errored - pt.errored} new transcode error(s) (total ${t.errored})`, 'See Tdarr > Transcode errors; originals are untouched on error');
  if (t.nodes && t.nodes.length && t.nodes.every((n) => n.paused)) add('tdarr-paused', 'info', 'Tdarr: all nodes paused', 'Normal 17:00-23:00 PT or when the 3 TB guard holds');
  if (t.done != null) {
    if (pt.done !== t.done || !pt.doneAt) sd.tdarr[host] = { ...pt, done: t.done, doneAt: now, errored: t.errored };
    else { sd.tdarr[host].errored = t.errored; if (t.queued > 0 && !(t.nodes || []).every((n) => n.paused) && now - pt.doneAt > S.TDARR_IDLE_H * H) add('tdarr-idle', 'warning', `Tdarr: no file finished in ${Math.round((now - pt.doneAt) / H)} h with ${t.queued} queued`, 'Workers may be stuck; check the Tdarr node page'); }
  }
}

// ---- edge: Tailscale and certificates ----
if (r.edge) {
  const ts = r.edge.tailscale || {};
  if (ts.online === false) add('ts-offline', 'critical', 'Tailscale subnet router is offline', 'Remote access is down (Plex port-forward still works)');
  if (ts.key_days_left != null && ts.key_days_left < S.KEY_WARN_DAYS) add('ts-key', ts.key_days_left < 3 ? 'critical' : 'warning', `Tailscale node key expires in ${ts.key_days_left} day(s)`, 'Re-authenticate the lab-router node or disable key expiry in the Tailscale admin console');
  if (ts.online && !(ts.routes || []).includes('192.168.1.0/24')) add('ts-route', 'critical', 'Tailscale is up but the 192.168.1.0/24 route is not advertised or approved', 'Approve the subnet route in the admin console');
  for (const c of r.edge.certs || []) {
    if (!c.ok) add(`cert:${c.host}`, 'critical', `${c.host}: no valid certificate`, 'TSDProxy could not get or renew it; check the tsdproxy logs');
    else if (c.days_left < S.CERT_WARN_DAYS) add(`cert:${c.host}`, 'warning', `${c.host}: certificate expires in ${c.days_left} day(s)`, 'TSDProxy renews automatically; watch it');
  }
}

// ---- trend point once an hour, kept 7 days ----
const tr = sd.trends[host] = (sd.trends[host] || []).filter((p) => now - p.t < 7 * 24 * H);
if (!tr.length || now - tr[tr.length - 1].t >= H - 60e3) {
  tr.push({ t: now, mem: sys.mem ? sys.mem.used_pct : null, restarts: restartsTotal, backup: b.newest_bytes || null,
    tdarr_done: md.tdarr ? md.tdarr.done : null, tdarr_queued: md.tdarr ? md.tdarr.queued : null,
    stuck: ['sonarr', 'radarr'].reduce((n, a) => n + ((md[a] && md[a].stuck) || []).length, 0) });
}
// Keep the report (minus bulky lists) as the previous one for the next run.
const compact = JSON.parse(JSON.stringify(r));
if (compact.docker && compact.docker.containers) compact.docker.containers = compact.docker.containers.map((c) => ({ name: c.name, service: c.service, state: c.state, health: c.health, restarts: c.restarts }));
compact.received = now;
sd.last[host] = compact;
return [{ json: { host, scope: host, ts: now, issues } }];
