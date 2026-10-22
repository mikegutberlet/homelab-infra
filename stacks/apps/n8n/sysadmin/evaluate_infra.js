// Evaluate infra: every 15 minutes, the read-only views that no VM agent has: Proxmox
// (cluster/resources, cluster/status, per-node status), TrueNAS (pool, DeepData dataset,
// alerts), Scrutiny (SMART summary), and which VMs have gone silent.
const sd = $getWorkflowStaticData('global');
sd.last = sd.last || {}; sd.trends = sd.trends || {};
const S = SETTINGS;
const issues = [];
const add = (...a) => issues.push(issue('infra', ...a));
const grab = (name) => { try { return $(name).first().json; } catch (e) { return null; } };
const failed = (j) => !j || j.error;
// n8n keeps the HTTP node's error on the item when "continue on error" is set.
const errText = (j) => (j && j.error && (j.error.message || JSON.stringify(j.error).slice(0, 200))) || 'no answer';

// ---- silent VMs ----
for (const h of S.HOSTS) {
  const last = sd.last[h];
  const age = last && last.received ? (now - last.received) / 60e3 : null;
  if (age == null) add(`silent:${h}`, 'warning', `${h} has never reported`, 'The homelab-agent timer is not deployed there yet, or cannot reach n8n');
  else if (age > S.SILENT_AFTER_MIN) add(`silent:${h}`, 'critical', `${h} has not reported for ${Math.round(age)} min`, 'VM down, Docker stuck, or the timer failed; check Proxmox and journalctl -u homelab-agent');
}

// ---- Proxmox ----
const res = grab('PVE resources');
if (failed(res)) add('pve-api', 'warning', 'Proxmox API not answering', errText(res));
else {
  const data = res.data || [];
  for (const n of data.filter((x) => x.type === 'node')) {
    if (n.status !== 'online') add(`pve-node:${n.node}`, 'critical', `Proxmox host ${n.node} is ${n.status}`, 'Cluster is 2-node; VMs on it are down and the other host may lose quorum');
    const pct = n.maxmem ? Math.round(100 * n.mem / n.maxmem) : 0;
    if (pct >= S.MEM_WARN) add(`pve-mem:${n.node}`, 'warning', `${n.node}: host RAM ${pct}% used`, `${gb(n.maxmem - n.mem)} free of ${gb(n.maxmem)}; balloon floors are the lever`);
  }
  for (const v of data.filter((x) => x.type === 'qemu')) {
    if (S.PVE_EXPECT_RUNNING.includes(v.vmid) && v.status !== 'running') add(`vm-down:${v.vmid}`, 'critical', `VM ${v.vmid} ${v.name} is ${v.status} on ${v.node}`, 'Start it from the Proxmox UI (TrueNAS first if it is 110); nothing here starts VMs');
    if (S.PVE_EXPECT_STOPPED.includes(v.vmid) && v.status === 'running') add(`vm-up:${v.vmid}`, 'warning', `Old VM ${v.vmid} ${v.name} is running`, 'It should stay stopped (kept for rollback)');
  }
  for (const s of data.filter((x) => x.type === 'storage' && x.maxdisk)) {
    const pct = Math.round(100 * s.disk / s.maxdisk);
    const key = `pve-storage:${s.node}:${s.storage}`;
    if (pct >= S.DISK_CRIT) add(key, 'critical', `${s.node} storage ${s.storage} is ${pct}% full`, `${gb(s.maxdisk - s.disk)} free; VM disks and backups live here`);
    else if (pct >= S.DISK_WARN) add(key, 'warning', `${s.node} storage ${s.storage} is ${pct}% full`, `${gb(s.maxdisk - s.disk)} free`);
  }
}
const cs = grab('PVE cluster status');
if (!failed(cs)) {
  const cl = (cs.data || []).find((x) => x.type === 'cluster');
  if (cl && !cl.quorate) add('pve-quorum', 'critical', 'Proxmox cluster has lost quorum', 'No VM can start or migrate until both NUCs see each other (pvecm status)');
  for (const n of (cs.data || []).filter((x) => x.type === 'node' && !x.online)) add(`pve-offline:${n.name}`, 'critical', `Proxmox node ${n.name} offline in the cluster view`, n.ip || '');
}
// The HTTP node answers in the same order as the node list it ran for.
let nodeStatus = [], nodeList = [];
try { nodeStatus = $('PVE node status').all().map((i) => i.json); nodeList = $('PVE node list').all().map((i) => i.json); } catch (e) { nodeStatus = []; }
for (const [idx, st] of nodeStatus.entries()) {
  const d = st.data; if (!d || !d.swap) continue;
  const name = (nodeList[idx] && nodeList[idx].node) || '?';
  if (d.swap.used > S.SWAP_WARN_BYTES) add(`pve-swap:${name}`, 'warning', `Proxmox host ${name} is using ${gb(d.swap.used)} of swap`, 'RicksGarage runs TrueNAS 16 GB + edge + media; lower a balloon floor or move a VM');
}

// ---- TrueNAS ----
const pools = grab('NAS pools');
if (failed(pools)) add('nas-api', 'warning', 'TrueNAS API not answering', errText(pools));
else {
  const list = Array.isArray(pools) ? pools : (pools.data || []);
  for (const p of list) {
    if (p.status !== 'ONLINE' || p.healthy === false) add(`pool-state:${p.name}`, 'critical', `Pool ${p.name} is ${p.status}${p.healthy === false ? ' (unhealthy)' : ''}`, 'A disk may have dropped; look at zpool status before touching anything (RAIDZ1, one disk of margin)');
    const sc = p.scan || {};
    if (sc.errors > 0) add(`scrub-errors:${p.name}`, 'critical', `Scrub on ${p.name} found ${sc.errors} error(s)`, `state ${sc.state}, function ${sc.function}`);
    if (sc.state === 'SCANNING') add(`scrub-running:${p.name}`, 'info', `Scrub on ${p.name} ${Math.round(sc.percentage || 0)}% done`, sc.total_secs_left ? `about ${Math.round(sc.total_secs_left / 3600)} h left` : '');
    const end = sc.end_time && (sc.end_time.$date || Date.parse(sc.end_time));
    if (sc.state !== 'SCANNING' && end && now - end > S.SCRUB_WARN_DAYS * 24 * H) add(`scrub-old:${p.name}`, 'warning', `Last scrub on ${p.name} finished ${ago(end)} ago`, 'TrueNAS runs one every 35 days by default; check the scrub task');
  }
}
const ds = grab('NAS dataset');
if (!failed(ds) && ds.available) {
  const free = ds.available.parsed || Number(ds.available.rawvalue) || 0;
  const used = ds.used && (ds.used.parsed || Number(ds.used.rawvalue)) || 0;
  if (free < S.POOL_CRIT_TB * TB) add('pool-free', 'critical', `DeepData has ${tb(free)} free`, `Below the 3 TB Tdarr guard; ${tb(used)} used. Clear the holding folders or stop downloads`);
  else if (free < S.POOL_WARN_TB * TB) add('pool-free', 'warning', `DeepData has ${tb(free)} free`, `${tb(used)} used; the Tdarr guard pauses at 3 TB`);
  const tr = sd.trends.infra = (sd.trends.infra || []).filter((p) => now - p.t < 7 * 24 * H);
  if (!tr.length || now - tr[tr.length - 1].t >= H - 60e3) tr.push({ t: now, pool_free: free, pool_used: used });
}
const al = grab('NAS alerts');
if (!failed(al) && Array.isArray(al)) {
  for (const a of al.filter((x) => !x.dismissed)) {
    const sev = a.level === 'CRITICAL' || a.level === 'ALERT' || a.level === 'EMERGENCY' ? 'critical' : a.level === 'WARNING' || a.level === 'ERROR' ? 'warning' : 'info';
    add(`nas-alert:${a.id || a.klass || a.formatted.slice(0, 40)}`, sev, `TrueNAS ${a.level}: ${(a.formatted || '').split('\n')[0].slice(0, 120)}`, a.klass || '');
  }
}

// ---- Scrutiny (SMART) ----
const sc = grab('Scrutiny');
if (!failed(sc) && sc.data && sc.data.summary) {
  for (const [wwn, s] of Object.entries(sc.data.summary)) {
    const d = s.device || {}; const st = d.device_status || 0;
    const label = `${d.device_name || wwn} ${d.serial_number || ''} (${d.host_id || ''})`.trim();
    if (st & 1) add(`smart:${d.serial_number || wwn}`, 'critical', `Disk ${label}: SMART reports failure`, 'Plan a replacement; the pool is RAIDZ1');
    else if (st & 2) add(`smart:${d.serial_number || wwn}`, 'warning', `Disk ${label}: Scrutiny thresholds exceeded`, 'Rising reallocated/pending sectors or command timeouts; keep watching');
    const temp = s.smart && s.smart.temp;
    if (temp && temp >= 55) add(`temp:${d.serial_number || wwn}`, 'warning', `Disk ${label} at ${temp} C`, 'DAS airflow');
  }
}
return [{ json: { host: 'infra', scope: 'infra', ts: now, issues } }];
