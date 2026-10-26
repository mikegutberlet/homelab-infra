// Evaluate report: one VM's homelab-agent report in, the list of issues it raises out.
// Reads and updates the per-host history in static data (previous report, restart samples,
// hourly trend points). Rules live here; the decision about emails and fixes is in Decide.
const sd = $getWorkflowStaticData('global');
sd.last = sd.last || {}; sd.rs = sd.rs || {}; sd.trends = sd.trends || {}; sd.tdarr = sd.tdarr || {};
sd.held = sd.held || {}; sd.rsTotal = sd.rsTotal || {};
const r = $input.first().json.body || $input.first().json;
const host = r.host;
if (!host) return [{ json: { host: 'unknown', issues: [], ignored: true } }];
const prev = sd.last[host] || null;
const S = SETTINGS;
if (!r.system) {
  // The agent posted but could not build its report (agent v3 then sends agent_error). One critical;
  // "partial" makes Decide leave this VM's other issues as they were instead of counting misses.
  sd.last[host] = { ...(prev || {}), host, received: now, agent_error: r.agent_error || 'empty report' };
  return [{ json: { host, scope: host, ts: now, partial: true, issues: [issue(host, 'agent', 'critical', `${host}: homelab-agent could not build its report`, `${r.agent_error || 'The report arrived empty'}. Every other check of this VM is blind until it does; journalctl -u homelab-agent on the VM`)] } }];
}
const issues = [];
const add = (...a) => issues.push(issue(host, ...a));
const sys = r.system || {};
if ((r.errors || []).length) add('agent-sections', 'warning', `${host}: homelab-agent could not read ${r.errors.length} part(s) of its report`, `${r.errors.join('; ').slice(0, 400)}. The rules for those parts are blind this round`);

// Stops that ran (automatic, or confirmed in chat) hold the service down until it runs again;
// Decide never restarts a held service, and the missing-service rule only reminds.
for (const f of sd.fixlog || []) {
  if (f.host === host && f.verb === 'stop' && f.status === 'ok' && !f.held && now - f.t < 24 * H) {
    if (!sd.held[`${host}:${f.target}`]) sd.held[`${host}:${f.target}`] = { t: f.t, by: f.source === 'chat' ? 'chat' : 'sysadmin', id: f.id };
    f.held = true;
  }
}

// ---- system ----
if (sys.mem && sys.mem.used_pct >= S.MEM_CRIT) add('mem', 'critical', `${host}: memory ${sys.mem.used_pct}% used`, `${gb(sys.mem.available)} available of ${gb(sys.mem.total)}`);
else if (sys.mem && sys.mem.used_pct >= S.MEM_WARN) add('mem', 'warning', `${host}: memory ${sys.mem.used_pct}% used`, `${gb(sys.mem.available)} available`);
// Swap: what is left in swap after a spike is harmless (it stays there for days); pages read back
// in from swap (pswpin, agent v3) mean the VM is short of RAM right now.
const ramAdvice = 'VMs have fixed memory (no ballooning): find the biggest container with docker stats, cap it (mem_limit), or give the VM more RAM in tofu/terraform.tfvars';
const psw = sys.swap || {}, ppsw = (prev && prev.system && prev.system.swap) || {};
if (psw.pswpin != null && ppsw.pswpin != null && psw.pswpin >= ppsw.pswpin && prev.received) {
  const mb = ((psw.pswpin - ppsw.pswpin) * 4096) / 1e6, min = Math.max(1, (now - prev.received) / 60e3);
  if (mb > (S.SWAP_IN_WARN_MB * min) / 15) add('swap', 'warning', `${host}: ${Math.round(mb)} MB read back from swap in ${Math.round(min)} min`, `Short of RAM right now (${gb(psw.used)} of ${gb(psw.total)} swap in use). ${ramAdvice}`);
} else if (psw.pswpin == null && psw.total && psw.used > psw.total / 2) add('swap', 'warning', `${host}: ${gb(psw.used)} of ${gb(psw.total)} swap in use`, ramAdvice);
if (sys.load && sys.cores && sys.load[2] > S.LOAD_FACTOR * sys.cores) add('load', 'warning', `${host}: 15-min load ${sys.load[2]} on ${sys.cores} cores`, 'Sustained for at least 15 minutes');
if (sys.uptime_s != null && sys.uptime_s < 1200) add('rebooted', 'warning', `${host} restarted ${Math.round(sys.uptime_s / 60)} min ago`, 'Unplanned unless you did it; check the Proxmox task log');
if (sys.reboot_required) add('reboot-required', 'info', `${host}: a kernel or libc update is waiting for a reboot`, 'Power off cleanly from Proxmox when convenient (NUC hosts: never a host reboot)');
if (sys.pending_updates > 0) add('updates', 'info', `${host}: ${sys.pending_updates} package update(s) pending`, 'unattended-upgrades installs security fixes; the rest wait for apt upgrade');
if (sys.clock_synced === false) add('clock', 'warning', `${host}: clock not NTP-synced`, 'Certificates and Tailscale depend on correct time');

// ---- kernel (2026-10-23: a kernel BUG in NFS writeback froze a Tdarr copy, and a day later the
// Plex scanner that touched the same file; Plex looked up but its library and restarts hung) ----
const kn = r.kernel || null;
if (kn && kn.oopsed) add('kernel-oops', 'critical', `${host}: the kernel crashed since boot`, `${kn.last_oops || 'taint flag D is set'}. Processes it was serving can hang for good; restart the VM from Proxmox when you can (qm reboot, or qm stop + start if it hangs)`);
// A D-state process seen in two reports in a row (15+ minutes) is stuck, not busy. what = ps lstart (5 words) + name.
const prevStuck = new Set(((prev && prev.kernel && prev.kernel.stuck) || []).map((p) => `${p.pid} ${p.what}`));
const stuckNow = ((kn && kn.stuck) || []).filter((p) => prevStuck.has(`${p.pid} ${p.what}`));
if (stuckNow.length) add('stuck-io', 'critical', `${host}: ${stuckNow.length} process(es) stuck in uninterruptible I/O`, `${stuckNow.slice(0, 5).map((p) => { const w = p.what.split(' '); return `${w.slice(5).join(' ')} (pid ${p.pid}, since ${w.slice(1, 4).join(' ')})`; }).join('; ')}. Usually a hung NAS write; they cannot be killed, and a container that holds one cannot restart. If the NAS answers, only a VM restart clears it`);

// ---- disks and mounts ----
for (const d of r.disks || []) {
  if (d.missing) { add(`disk-missing:${d.mount}`, 'critical', `${host}: ${d.mount} is not mounted`, 'Its disk did not come back after a restart; whatever writes there now fills the system disk (/etc/fstab, lsblk)'); continue; }
  if (S.DISK_SKIP.includes(d.mount)) continue;
  // Percent and free space both: 80% of gpu's 96 GB disk still leaves 19 GB.
  if (d.used_pct >= 97 || (d.used_pct >= S.DISK_CRIT && d.avail < S.DISK_CRIT_FREE_GB * GB)) add(`disk:${d.mount}`, 'critical', `${host}: ${d.mount} is ${d.used_pct}% full`, `${gb(d.avail)} left; app databases live here`);
  else if (d.used_pct >= S.DISK_WARN && d.avail < S.DISK_WARN_FREE_GB * GB) add(`disk:${d.mount}`, 'warning', `${host}: ${d.mount} is ${d.used_pct}% full`, `${gb(d.avail)} left${host === 'gpu' && d.mount === '/' ? '; the Plex, Emby, Tdarr and Unmanic transcode caches live here' : ''}`);
}
const m = r.mounts || {};
// m.nfs is null on VMs without an NFS library mount (edge, ai); the agent only reports it where hosts.yml has nfs_mounts.
// responding === false (agent v3): the mount hung, so a remount would hang too and recreating apps would not help.
if (m.nfs && !(m.nfs.mounted && m.nfs.nonempty)) {
  if (m.nas_tcp_2049 && m.nfs.responding === false) add('nfs', 'critical', `${host}: /mnt/nfs hangs although TrueNAS answers on 2049`, 'The NFS service on TrueNAS is stuck or overloaded (a scrub, a failing disk); look there first. A remount from the VM would hang too, so nothing runs from here');
  else if (m.nas_tcp_2049) add('nfs', 'critical', `${host}: /mnt/nfs is ${m.nfs.mounted ? 'mounted but empty' : 'not mounted'} while the NAS is up`, 'Apps are running against an empty library path', { verb: 'remount-nas', target: 'nfs' });
  // The infra check raises one critical when several VMs lose the NAS; per VM it is a warning.
  else add('nfs', 'warning', `${host}: /mnt/nfs is not available and TrueNAS does not answer on 2049`, 'Waiting for the NAS; nothing to fix from here');
}
if (m.backups_mounted === false && m.nas_tcp_2049) add('backups-mount', 'warning', `${host}: /mnt/backups is not mounted`, 'Tonight\'s backup will fail unless the automount comes back');

// ---- backups ----
const b = r.backup || {};
if (b.last_success) {
  const h = (now / 1000 - b.last_success) / 3600;
  if (h > S.BACKUP_CRIT_H) add('backup-age', 'critical', `${host}: last good backup ${Math.round(h)} h ago`, `Newest folder ${b.newest || 'none'}; check journalctl -u homelab-backup`);
  else if (h > S.BACKUP_WARN_H) add('backup-age', 'warning', `${host}: last good backup ${Math.round(h)} h ago`, 'The 03:30 timer may have been missed');
} else if (prev) add('backup-none', 'warning', `${host}: no successful backup in the journal`, 'Expected after a fresh VM or a journal rotation; otherwise run homelab-backup by hand');
if (b.last_rc && b.last_rc !== 0) add('backup-rc', 'warning', `${host}: the last backup run exited ${b.last_rc}`, 'journalctl -u homelab-backup on the VM has the reason');
if (b.newest_bytes && b.previous_bytes && b.newest_bytes < b.previous_bytes / 2) add('backup-size', 'warning', `${host}: newest backup is ${size(b.newest_bytes)}, previous was ${size(b.previous_bytes)}`, 'A backup that halved usually skipped something');

// ---- containers ----
const dk = r.docker || {};
sd.rs[host] = sd.rs[host] || {};
const prevHealth = {};
for (const c of (prev && prev.docker && prev.docker.containers) || []) prevHealth[c.name] = c.health;
// Restarts between two samples. RestartCount alone misses most of them: a recreate starts a new
// container at 0, and Docker zeroes the count on every API start (autoheal, the ops door), so the
// container id (agent v3) and the start time are compared too. A recreate itself is not counted.
const incOf = (a, b) => {
  if (a.id && b.id && a.id !== b.id) return b.r;
  if (b.r < a.r) return b.r + 1;
  return b.r - a.r + (b.r === a.r && a.s && b.s && a.s !== b.s ? 1 : 0);
};
let restartsNow = 0;
for (const c of dk.containers || []) {
  const svc = c.service || c.name;
  const canFix = !S.NEVER_RESTART.includes(svc);
  // A held service that runs again was started by someone: the hold ends.
  if (c.state === 'running' && sd.held[`${host}:${svc}`]) delete sd.held[`${host}:${svc}`];
  // Restart samples over the last 70 minutes.
  const samples = (sd.rs[host][c.name] || []).filter((s) => now - s.t < 70 * 60e3);
  samples.push({ t: now, r: c.restarts || 0, id: c.id || null, s: c.started_at || null });
  sd.rs[host][c.name] = samples;
  let delta = 0;
  for (let k = 1; k < samples.length; k++) delta += incOf(samples[k - 1], samples[k]);
  if (samples.length > 1) restartsNow += incOf(samples[samples.length - 2], samples[samples.length - 1]);
  if (delta >= S.RESTARTS_CRIT) add(`crashloop:${c.name}`, 'critical', `${host}: ${c.name} restarted ${delta} times in the last hour`, `State ${c.state}, exit code ${c.exit_code}; stopping beats hammering the NAS`, canFix ? { verb: 'stop', target: svc, escalate: 'restart' } : null);
  else if (delta >= S.RESTARTS_WARN) add(`restarts:${c.name}`, 'warning', `${host}: ${c.name} restarted ${delta} times in the last hour`, `State ${c.state}, health ${c.health}${svc === 'qbittorrent' || svc === 'gluetun' ? '; usually the PIA tunnel dropping (qBittorrent\'s health check restarts it until gluetun is back)' : ''}`);
  if (c.health === 'unhealthy') {
    if (prevHealth[c.name] === 'unhealthy') add(`unhealthy:${c.name}`, 'critical', `${host}: ${c.name} unhealthy for 30 minutes`, 'autoheal has not brought it back', canFix ? { verb: 'restart', target: svc } : null);
    else add(`unhealthy:${c.name}`, 'warning', `${host}: ${c.name} is unhealthy`, 'autoheal gets one more interval');
  }
}
sd.rsTotal[host] = (sd.rsTotal[host] || 0) + restartsNow;
// An expected service that is not running: a warning the first time (a deploy or a maintenance
// playbook may have stopped it for a minute), a critical with a restart when it is still down 15
// minutes later. A service stopped on purpose is only a reminder, with the command that starts it.
const prevMissing = (prev && prev.docker && prev.docker.missing) || [];
for (const missing of dk.missing || []) {
  const [stack, svc] = missing.split('/');
  const c = (dk.containers || []).find((x) => x.service === svc);
  const state = c ? `${c.state} (exit ${c.exit_code})` : 'no container';
  const held = sd.held[`${host}:${svc}`];
  if (held) add(`missing:${svc}`, 'warning', `${host}: ${svc} stopped ${held.by === 'chat' ? 'from chat' : 'by the sysadmin after a crash loop'} at ${fmtT(held.t)}`, `It stays stopped until you start it: cd /opt/stacks/${stack} && sudo docker compose up -d ${svc}`);
  else if (!prevMissing.includes(missing)) add(`missing:${svc}`, 'warning', `${host}: ${svc} is ${state}`, 'Expected by stack_services / the compose file; if it is still down at the next check it gets a restart');
  else add(`missing:${svc}`, 'critical', `${host}: ${svc} should be running but is ${state}`, 'Expected by stack_services / the compose file', S.NEVER_RESTART.includes(svc) ? null : { verb: 'restart', target: svc });
}
if (dk.reclaimable_bytes > S.RECLAIM_WARN_GB * GB) add('docker-space', 'warning', `${host}: ${gb(dk.reclaimable_bytes)} of dangling images and build cache`, 'docker image prune (dangling only) frees it', { verb: 'docker-prune', target: 'docker' });

// ---- media apps ----
const md = r.media || {};
const pmd = (prev && prev.media) || {};
// What an arr health check means and what to do about it; the check name alone says little.
const ARR_HINT = {
  IndexerStatusCheck: 'Prowlarr backed the indexer off; the sysadmin re-tests it when the backoff ends',
  IndexerLongTermStatusCheck: 'Indexers failing for over 6 h: check the indexer site and its API limit in Prowlarr',
  IndexerSearchCheck: 'No indexer has search enabled: Prowlarr > Indexers',
  IndexerRssCheck: 'No indexer has RSS enabled: Prowlarr > Indexers',
  DownloadClientCheck: 'Cannot reach SABnzbd or qBittorrent; for qBittorrent this is usually the PIA tunnel reconnecting, and it clears by itself',
  DownloadClientStatusCheck: 'A download client keeps failing; for qBittorrent this is usually the PIA tunnel reconnecting, and it clears by itself',
  RootFolderCheck: 'A root folder no longer exists: remove it in Settings > Media Management > Root Folders, or move the items that still point there',
  RemovedSeriesCheck: 'TheTVDB removed these series: delete them in Sonarr, or replace each with its new entry',
  RemovedMovieCheck: 'TMDb removed these movies: delete them in Radarr, or replace each with its new entry',
  ImportMechanismCheck: 'Completed download handling is off, or the download folder is not visible to the app',
  ApiKeyValidationCheck: 'The API key in config.xml is missing or too short',
};
const hint = (hc) => (ARR_HINT[hc.source] ? `${ARR_HINT[hc.source]} (${hc.source})` : hc.source);
// A health error has to show in two reports in a row: DownloadClientCheck blinks with every tunnel reconnect.
const wasError = (pa, hc) => ((pa && pa.health) || []).some((x) => x.type === 'error' && x.source === hc.source);
// Stuck queue items that need the same decision share one issue: the episodes of one series (a
// season pack is one queue item per episode, and deleting one drops the whole download) or one
// movie, stuck in the same state with the same message once paths and episode numbers are left out.
const same = (msg) => msg.replace(/\/\S+/g, '<path>').replace(/\bS\d+E\d+\b/gi, '').replace(/\d+/g, '#');
const short = (x) => { let h = 5381; for (const ch of x) h = ((h * 33) ^ ch.charCodeAt(0)) >>> 0; return h.toString(36); };
for (const app of ['sonarr', 'radarr']) {
  const a = md[app]; if (!a) continue;
  for (const hc of a.health || []) if (hc.type === 'error' && wasError(pmd[app], hc)) add(`${app}-health:${hc.source}`, 'warning', `${app}: ${hc.message}`, hint(hc));
  const stuck = a.stuck || [];
  if (stuck.length > S.QUEUE_CRIT) add(`${app}-queue`, 'critical', `${app}: ${stuck.length} queue items stuck or failed`, stuck.slice(0, 5).map((q) => q.title).join('; '));
  const groups = new Map();
  for (const q of stuck) {
    const msg = (q.messages || []).join(' | ');
    let kind = null;
    if ((q.tdstate === 'importBlocked' || q.tdstate === 'importPending' || q.status === 'failed' || q.tds === 'error') && (q.age_h || 0) >= S.STUCK_RETRY_H) kind = 'blocked';
    else if (q.tds === 'warning' && (q.age_h || 0) >= S.STUCK_RETRY_H) kind = 'decision';
    else if (q.tdstate === 'downloading' && (q.age_h || 0) >= S.STUCK_BLOCKLIST_H && q.size && q.sizeleft === q.size)
      add(`${app}-dead:${q.id}`, 'warning', `${app}: "${q.title}" has downloaded nothing in ${q.age_h} h`, `${q.protocol}; blocklist and search again`, S.BLOCKLIST_ENABLED ? { verb: 'arr-blocklist', target: `${app} ${q.id}`, app, id: q.id } : null);
    if (!kind) continue;
    const gk = `${q.ref == null ? q.title : q.ref}|${kind}|${q.tdstate || q.status}|${same(msg)}`;
    if (!groups.has(gk)) groups.set(gk, { kind, msg, items: [] });
    groups.get(gk).items.push(q);
  }
  for (const [gk, g] of groups) {
    const q = g.items[0], n = g.items.length;
    const age = Math.max(...g.items.map((x) => x.age_h || 0));
    const what = n > 1 ? `"${q.title}" and ${n - 1} more of the same ${app === 'sonarr' ? 'series' : 'movie'}` : `"${q.title}"`;
    const key = `${app}-stuck:r${q.ref == null ? q.id : q.ref}-${short(gk)}`;
    if (g.kind === 'decision') { add(key, 'warning', `${app}: ${what} needs a decision (${age} h)`, g.msg || 'no message'); continue; }
    // One arr-retry per group and run, on its first item: a "no files found" import gets a fresh search.
    const retryable = /no files found|not found|missing|does not exist|was not found/i.test(g.msg);
    // A release the library already has in better quality never imports: blocklist it (removes the
    // download, so the same release is not grabbed again) once it has sat for STUCK_BLOCKLIST_H.
    const noUpgrade = S.BLOCKLIST_ENABLED && age >= S.STUCK_BLOCKLIST_H && /not an upgrade|not a custom format upgrade/i.test(g.msg);
    const fix = retryable ? { verb: 'arr-retry', target: `${app} ${q.id}`, app, id: q.id }
      : noUpgrade ? { verb: 'arr-blocklist', target: `${app} ${q.id}`, app, id: q.id } : null;
    add(key, 'warning', `${app}: ${what} ${q.tdstate || q.status} for ${age} h`, g.msg || 'no message', fix);
  }
}
// Issues keyed by a bare queue id (before 2026-10-24) are replaced by the groups above: drop them
// quietly instead of listing each one as resolved.
for (const k of Object.keys(sd.issues || {})) if (new RegExp(`^${host}:(sonarr|radarr)-stuck:\\d+$`).test(k)) delete sd.issues[k];
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
  if (s.diskspace_gb != null && s.diskspace_gb < S.SAB_DISK_CRIT_GB) add('sab-disk', 'critical', `SABnzbd's temp disk has only ${s.diskspace_gb} GB free`, 'Downloads pause at 20 GB; finished jobs that will not import are the usual cause (check the Radarr/Sonarr queues and /srv/usenet-incomplete)');
  else if (s.diskspace_gb != null && s.diskspace_gb < S.SAB_DISK_WARN_GB) add('sab-disk', 'warning', `SABnzbd's temp disk is down to ${s.diskspace_gb} GB free`, 'Pauses at 20 GB');
  if (s.paused && s.queue_count > 0) add('sab-paused', 'warning', `SABnzbd is paused with ${s.queue_count} item(s) queued`, s.pause_int && s.pause_int !== '0' ? `timed pause, ${s.pause_int} min left` : 'manual or disk pause');
  if ((s.failed_24h || []).length) add('sab-failed', 'warning', `SABnzbd: ${s.failed_24h.length} failed download(s) in 24 h`, s.failed_24h.slice(0, 5).map((f) => `${f.name}: ${f.fail_message || ''}`).join('; '));
}
if (md.qbittorrent) {
  const q = md.qbittorrent;
  if (q.exit_ip && q.vpn_ok === false) add('qb-vpn', 'critical', 'qBittorrent is NOT behind the VPN', `exit IP ${q.exit_ip} equals the home IP`, { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
  else if (!q.exit_ip) add('qb-vpn', 'warning', 'gluetun has no internet through PIA', 'tunnel down or reconnecting', { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
  // Agent v3: forwarded_port is the port PIA forwarded (gluetun's file), listen_port qBittorrent's
  // own setting, qb_api whether qBittorrent answered at all. Older agents sent no listen_port.
  if (q.listen_port !== undefined && q.exit_ip && q.vpn_ok) {
    if (q.forwarded_port == null) add('qb-port', 'warning', 'PIA has no forwarded port', 'gluetun lost port forwarding; seeding and torrent speed suffer', { verb: 'restart', target: 'gluetun', then: 'qbittorrent' });
    else if (q.qb_api === false) add('qb-api', 'warning', 'qBittorrent does not answer inside gluetun', 'Its health check makes autoheal restart it; it only needs you if this lasts');
    else if (q.listen_port && q.listen_port !== q.forwarded_port) add('qb-port', 'warning', `qBittorrent listens on ${q.listen_port}, PIA forwarded ${q.forwarded_port}`, 'gluetun hands the port to qBittorrent when forwarding starts; a gluetun restart repeats that (the chat can propose it)');
  }
  if ((q.errored || []).length) add('qb-errored', 'warning', `qBittorrent: ${q.errored.length} torrent(s) in error`, q.errored.slice(0, 5).map((t) => `${t.name} (${t.state})`).join('; '));
  if ((q.stalled_3d || []).length) add('qb-stalled', 'info', `qBittorrent: ${q.stalled_3d.length} torrent(s) stalled over 3 days`, q.stalled_3d.slice(0, 5).map((t) => t.name).join('; '));
}
if (md.tdarr) {
  const t = md.tdarr; const pt = sd.tdarr[host] || {};
  if (t.done == null) add('tdarr-api', 'warning', 'Tdarr\'s API does not answer the agent', 'Tdarr is down, or /root/tdarr-api-key on gpu no longer matches its API key; the Tdarr checks are blind');
  if (t.errored != null && pt.errored != null && t.errored > pt.errored) add('tdarr-errors', 'warning', `Tdarr: ${t.errored - pt.errored} new transcode error(s) (total ${t.errored})`, 'See Tdarr > Transcode errors; originals are untouched on error');
  if (t.nodes && t.nodes.length && t.nodes.every((n) => n.paused)) add('tdarr-paused', 'info', 'Tdarr: all nodes paused', 'Paused by hand, or the 3 TB guard holds');
  // Idle time counts only the library schedule's hours: it pauses 17:00-23:00 PT every evening.
  const schedH = (from) => { let n = 0; for (let x = Math.max(from, now - 7 * 24 * H); x < now; x += 15 * 60e3) { const h = ptHour(x); if (h < S.TDARR_PAUSE_HOURS[0] || h >= S.TDARR_PAUSE_HOURS[1]) n += 0.25; } return n; };
  if (t.done != null) {
    if (pt.done !== t.done || !pt.doneAt) sd.tdarr[host] = { ...pt, done: t.done, doneAt: now, errored: t.errored };
    else {
      sd.tdarr[host].errored = t.errored;
      const idle = schedH(pt.doneAt);
      if (t.queued > 0 && !(t.nodes || []).every((n) => n.paused) && idle > S.TDARR_IDLE_H) add('tdarr-idle', 'warning', `Tdarr: no file finished in ${Math.round(idle)} scheduled hours with ${t.queued} queued`, 'Workers may be stuck (the 17:00-23:00 pause is not counted); check the Tdarr node page');
    }
  }
}

// ---- Plex: can apps outside the LAN find it? (gpu only; the agent sends plex where Plex runs) ----
// 2026-10-25: Remote Access had been off since the Oct 21 power cut. plex.tv refused Plex's
// check-ins and then stopped listing it, so the Roku (which finds servers through plex.tv)
// showed nothing while the container stayed healthy and Kuma's LAN probe stayed green.
const px = r.plex;
if (px && px.answering) {
  const pp = (prev && prev.plex) || {};
  const on = S.PLEX_REMOTE ? { verb: 'plex-remote-on', target: 'plex' } : null;
  if (!px.token || (px.sign_in && px.sign_in !== 'ok')) add('plex-signin', 'critical', `Plex is signed out of plex.tv (${px.sign_in || 'no token'})`, 'Apps cannot find it or log in; sign in again from Plex Web on the LAN (http://192.168.1.52:32400/web > Settings > General). Nothing is fixed from here');
  else if (S.PLEX_REMOTE && px.publish === false) add('plex-remote', 'critical', 'Plex Remote Access is switched off', 'plex.tv stops listing the server after a while, and then the Roku and other apps cannot find it, at home too', on);
  // One plex.tv answer without the server can be a blip: critical (and a re-publish) from the second.
  else if (px.listed === false && pp.listed !== false) add('plex-listed', 'warning', 'plex.tv does not list Plex (Tahoe) as a server', 'Apps that find servers through plex.tv see nothing; re-published if it is still missing at the next check');
  else if (px.listed === false) add('plex-listed', 'critical', 'plex.tv does not list Plex (Tahoe) as a server', 'The Roku and other apps that find servers through plex.tv see nothing', on);
  // A device Plex keeps turning away (401) holds a sign-in plex.tv no longer issues; only signing out
  // and in on that device fixes it (the Roku, 2026-10-25). Three or more in two reports in a row,
  // so one wrong PIN is not a warning. 127.0.0.1 is a remote app coming in through the Plex relay.
  const wasRefused = new Set((pp.refused || []).filter((x) => x.n >= 3).map((x) => x.ip));
  for (const x of (px.refused || []).filter((y) => y.n >= 3 && wasRefused.has(y.ip)))
    add(`plex-refused:${x.ip}`, 'warning', `Plex keeps refusing ${x.ip === '127.0.0.1' ? 'a remote app (through the relay)' : `the device at ${x.ip}`} (${x.n} times in 15 min)`, 'It holds a Plex sign-in that plex.tv no longer issues. In that device\'s Plex app: Settings > Sign out, then sign in again with the code at plex.tv/link');
  // The forward on the router cannot help behind carrier NAT (Starlink: router WAN 100.82.x).
  if (S.PLEX_REMOTE && px.publish && px.mapping_error) add('plex-nat', 'info', `Plex is not reachable directly from outside the house (${px.mapping_error})`, px.mapping_error === 'doublenat' ? 'The router itself sits behind another NAT (carrier NAT or a second router), so the 6996 forward cannot work; remote apps fall back to the slow Plex relay. At home and over Tailscale it is fine' : 'Remote apps fall back to the slow Plex relay; check the 6996 forward on the router');
}

// ---- logins and API keys (agent v2; fingerprints only) ----
// 2026-10-22: a Sonarr/Radarr password mismatch went unnoticed for hours. Now a changed login,
// a changed or rejected API key, a consumer holding a stale key, or repeated failed logins are
// raised the same quarter hour. Nothing here is auto-fixed: credentials are Goobs's call.
const lg = r.logins || {};
const plg = (prev && prev.logins) || {};
const lockedOut = 'Reset steps: wiki operations/locked-out.md (playbooks/arr-auth.yml)';
for (const app of ['sonarr', 'radarr', 'prowlarr']) {
  const a = lg[app]; if (!a) continue;
  const p = plg[app] || null;
  if (p && (a.auth_method !== p.auth_method || a.auth_required !== p.auth_required))
    add(`login-mode:${app}`, 'critical', `${app}: login mode changed to ${a.auth_method}/${a.auth_required} (was ${p.auth_method}/${p.auth_required})`, `Only a config.xml edit or a restore does this. ${lockedOut}`);
  if (p && p.users && a.users && a.users.fp !== p.users.fp)
    add(`login-users:${app}`, 'critical', `${app}: the web login changed (user${a.users.count === 1 ? '' : 's'} ${a.users.names.join(', ')})`, `Password or username differs from 15 minutes ago. If you did not change it: restore ${app}.db from /mnt/backups. ${lockedOut}`);
  if (a.auth_method === 'Forms' && a.users && a.users.count === 0)
    add(`login-nouser:${app}`, 'critical', `${app}: forms login is on but no user exists`, 'The next page load asks to create one; whoever gets there first owns the app');
  if (p && p.apikey_fp && a.apikey_fp && a.apikey_fp !== p.apikey_fp)
    add(`apikey:${app}`, 'critical', `${app}: API key changed`, 'Prowlarr, Bazarr, Recyclarr, Jellyseerr, Homepage and the agent all hold the old one; update them or restore config.xml');
  if (a.api_http != null && a.api_http !== 200)
    add(`apikey-rejected:${app}`, 'critical', `${app}: rejects its own API key (HTTP ${a.api_http})`, 'config.xml and the running app disagree; a restart usually reloads the file');
  // Counts are per log file; the first report after a deploy (or a log rotation) sets the baseline.
  const newFails = p && p.auth_failures != null && a.auth_failures >= p.auth_failures ? a.auth_failures - p.auth_failures : 0;
  if (newFails >= S.LOGIN_FAILS_CRIT)
    add(`login-failures:${app}`, 'critical', `${app}: ${newFails} failed web login(s) in the last 15 min`, `Last at ${a.last_failure || '?'} (VM local time). The stored login has ${p && p.users && a.users && a.users.fp === p.users.fp ? 'NOT changed' : 'changed'}; check the password-manager entry (user ${a.users ? a.users.names.join('/') : '?'}). ${lockedOut}`);
  else if (newFails > 0)
    add(`login-failures:${app}`, 'info', `${app}: ${newFails} failed web login(s)`, `Last at ${a.last_failure || '?'}`);
}
if (lg.sabnzbd) {
  const a = lg.sabnzbd, p = plg.sabnzbd || null;
  if (p && a.login_fp !== p.login_fp) add('login-users:sabnzbd', 'critical', `SABnzbd: the web login changed (${a.login_set ? 'set' : 'none'})`, `Was ${p.login_set ? 'set' : 'none'} 15 minutes ago. ${lockedOut}`);
  if (p && p.apikey_fp && a.apikey_fp && a.apikey_fp !== p.apikey_fp) add('apikey:sabnzbd', 'critical', 'SABnzbd: API key changed', 'Sonarr, Radarr, Listenarr, Chaptarr, Homepage and the agent hold the old one');
  if (a.api_http != null && a.api_http !== 200) add('apikey-rejected:sabnzbd', 'critical', `SABnzbd: rejects its own API key (HTTP ${a.api_http})`, 'sabnzbd.ini and the running app disagree');
}
// Consumers: every stored copy of an arr key must match the app's own key. Copies on another VM
// (Homepage on apps) are compared against the media VM's last report.
const own = {};
for (const h of [...new Set([host, ...Object.keys(sd.last)])]) for (const app of ['sonarr', 'radarr', 'prowlarr', 'sabnzbd']) {
  const x = ((h === host ? lg : (sd.last[h].logins || {}))[app]) || null;
  if (x && x.apikey_fp) own[app] = x.apikey_fp;
}
for (const [who, fpv] of Object.entries(lg.consumers || {})) {
  const [consumer, app] = who.split(':');
  if (own[app] && fpv !== own[app]) add(`apikey-stale:${who}`, 'critical', `${consumer} holds a stale ${app} API key`, `${consumer}'s copy differs from ${app}'s config; its calls fail with 401. Paste the current key into ${consumer}'s settings`);
}

// ---- edge: Tailscale and certificates ----
if (r.edge) {
  const ts = r.edge.tailscale || {};
  if (ts.online === false) add('ts-offline', 'critical', 'Tailscale subnet router is offline', 'Remote access is down (Plex port-forward still works)');
  if (ts.key_days_left != null && ts.key_days_left < S.KEY_WARN_DAYS) add('ts-key', ts.key_days_left < 3 ? 'critical' : 'warning', `Tailscale node key expires in ${ts.key_days_left} day(s)`, 'Re-authenticate the lab-router node or disable key expiry in the Tailscale admin console');
  if (ts.online && !(ts.routes || []).includes('192.168.1.0/24')) add('ts-route', 'critical', 'Tailscale is up but the 192.168.1.0/24 route is not advertised or approved', 'Approve the subnet route in the admin console');
  for (const c of r.edge.certs || []) {
    // why (agent v3): "not on the tailnet" (TSDProxy has no node for the name) or "no TLS answer".
    if (!c.ok) add(`cert:${c.host}`, 'critical', `${c.host}: no valid certificate${c.why ? ` (${c.why})` : ''}`, c.why === 'not on the tailnet' ? 'TSDProxy has no Tailscale node for this name: a name clash (it shows up as name-1 in the admin console) or the proxy entry failed; check the tsdproxy logs' : 'TSDProxy could not get or renew it; check the tsdproxy logs');
    else if (c.days_left < S.CERT_WARN_DAYS) add(`cert:${c.host}`, 'warning', `${c.host}: certificate expires in ${c.days_left} day(s)`, 'TSDProxy renews automatically; watch it');
  }
}

// ---- trend point once an hour, kept 7 days ----
const tr = sd.trends[host] = (sd.trends[host] || []).filter((p) => now - p.t < 7 * 24 * H);
if (!tr.length || now - tr[tr.length - 1].t >= H - 60e3) {
  tr.push({ t: now, mem: sys.mem ? sys.mem.used_pct : null, rs_total: sd.rsTotal[host], backup: b.newest_bytes || null,
    tdarr_done: md.tdarr ? md.tdarr.done : null, tdarr_queued: md.tdarr ? md.tdarr.queued : null,
    stuck: ['sonarr', 'radarr'].reduce((n, a) => n + ((md[a] && md[a].stuck) || []).length, 0) });
}
// Keep the report (minus bulky lists) as the previous one for the next run.
const compact = JSON.parse(JSON.stringify(r));
if (compact.docker && compact.docker.containers) compact.docker.containers = compact.docker.containers.map((c) => ({ name: c.name, service: c.service, state: c.state, health: c.health, restarts: c.restarts }));
compact.received = now;
sd.last[host] = compact;
return [{ json: { host, scope: host, ts: now, issues } }];
