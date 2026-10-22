// Shared constants for the "Homelab sysadmin" workflow. docs/build_sysadmin_workflow.py pastes
// this file at the top of every Code node, so change it here and rebuild, not inside n8n.
const SETTINGS = {
  // Round one runs report-only: fixes are decided and logged as "would run", nothing is executed.
  FIXES_ENABLED: false,
  // arr-blocklist stays off until arr-retry has run clean for a week (Goobs, 2026-10-22).
  BLOCKLIST_ENABLED: false,
  // VMs that must report; a silent one is itself a critical issue.
  HOSTS: ['edge', 'media', 'gpu', 'apps', 'ai'],
  HOST_IP: { edge: '192.168.1.50', media: '192.168.1.51', gpu: '192.168.1.52', apps: '192.168.1.53', ai: '192.168.1.54' },
  SILENT_AFTER_MIN: 40,
  // Per target (host + verb + name): at most one fix an hour and three a day.
  FIX_PER_HOUR: 1,
  FIX_PER_DAY: 3,
  // Services the ops door never restarts (mirrors ops_never in ansible group_vars).
  NEVER_RESTART: ['n8n', 'postgres', 'redis', 'autoheal', 'tailscale', 'immich-db', 'immich-redis', 'romm-db', 'authentik-db', 'authentik-redis', 'scrutiny-influx'],
  // Critical issues are emailed at once, then again only every 6 hours while they persist.
  EMAIL_REPEAT_H: 6,
  // Thresholds
  POOL_WARN_TB: 4, POOL_CRIT_TB: 3,          // DeepData free; Tdarr pauses below 3 TB
  DISK_WARN: 80, DISK_CRIT: 90,              // percent used, VM disks and Proxmox storage
  MEM_WARN: 90, MEM_CRIT: 95,                // percent used
  SWAP_WARN_BYTES: 256 * 1024 * 1024,
  LOAD_FACTOR: 2,                            // 15-min load over this x cores = warning
  BACKUP_WARN_H: 26, BACKUP_CRIT_H: 50,
  RESTARTS_WARN: 3, RESTARTS_CRIT: 10,       // per container per hour
  RECLAIM_WARN_GB: 20,
  QUEUE_CRIT: 20,                            // stuck arr items
  STUCK_RETRY_H: 24, STUCK_BLOCKLIST_H: 48,
  // SABnzbd's temp disk is 200 GB (scratch disk, 2026-10-22); SABnzbd itself pauses at 20 GB free.
  SAB_DISK_WARN_GB: 30, SAB_DISK_CRIT_GB: 22,
  TDARR_IDLE_H: 6,
  LOGIN_FAILS_CRIT: 3,                       // failed arr web logins per 15 min that email at once
  KEY_WARN_DAYS: 14, CERT_WARN_DAYS: 14,
  SCRUB_WARN_DAYS: 45,
  PVE_EXPECT_RUNNING: [110, 201, 202, 203, 204, 205],
  PVE_EXPECT_STOPPED: [113, 117],
  TZ: 'America/Los_Angeles',
  // Chat page (sysadmin.<tailnet>). CHAT_FIXES_ENABLED lets a fix proposed in chat run after Goobs
  // presses Confirm; it is separate from FIXES_ENABLED, which gates the automatic ones.
  // On since 2026-10-22 20:32Z (Goobs: "CHAT_FIXES_ENABLED on"); false = chat proposes but Confirm does nothing.
  CHAT_FIXES_ENABLED: true,
  CHAT_FIX_PER_HOUR: 2,                      // per target, counting automatic fixes too
  CHAT_FIX_PER_DAY: 10,                      // all chat fixes together
  CHAT_CONFIRM_MIN: 15,                      // a proposal expires after this
};
const TB = 1e12, GB = 1e9, H = 3600e3;
const now = Date.now();
const fmtT = (ms) => new Date(ms).toLocaleString('en-US', { timeZone: SETTINGS.TZ, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const gb = (b) => `${(b / GB).toFixed(1)} GB`;
const tb = (b) => `${(b / TB).toFixed(2)} TB`;
const ago = (ms) => { const h = (now - ms) / H; return h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`; };
// One issue: key is stable across runs so the store can track first/last seen and emails.
const issue = (host, key, sev, title, detail, fix) => ({ host, key: `${host}:${key}`, sev, title, detail: detail || '', fix: fix || null });
