#!/bin/bash
# Reporter for the n8n "Homelab sysadmin" workflow. Collects this VM's facts every 15 minutes
# and POSTs one JSON document to n8n. Read-only: nothing here restarts, deletes or changes
# anything. App API keys are read from the app's own config under /opt/appdata and used only
# against localhost (Plex's token also against plex.tv, its issuer); they never reach n8n.
#
# Usage: homelab-agent [--print]     (--print writes the report to stdout instead of n8n)
set -uo pipefail

CONF=/etc/homelab-agent.conf           # written by Ansible (role ops): REPORT_URL, STACKS, EXPECTED
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"
REPORT_URL=${REPORT_URL:-http://192.168.1.53:5678/webhook/homelab/report}
STACKS=${STACKS:-}
EXPECTED=${EXPECTED:-}                 # "stack/service stack/service ..." that must be running
NAS_IP=${NAS_IP:-192.168.1.209}
NFS_EXPECTED=${NFS_EXPECTED:-true}       # false on VMs without an NFS library mount (edge, ai)
EXTRA_DISKS=${EXTRA_DISKS:-}           # more mount points to report, e.g. the SABnzbd scratch disk
CERT_SKIP=${CERT_SKIP:-}               # ts.net names not to probe (edge: services that are off on purpose)
APPDATA=/opt/appdata
AGENT_VERSION=3

now=$(date +%s)
j() { jq -c "$@"; }
# curl with short timeouts; prints {} unless the answer is one complete JSON document, so a reply
# cut off by the timeout (or an HTML error page) never reaches jq downstream.
get() { local o; o=$(curl -fsS -m 15 "$@" 2>/dev/null) && [ -n "$o" ] && jq empty <<<"$o" 2>/dev/null && printf '%s\n' "$o" || echo '{}'; }
num() { [[ "${1:-}" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && echo "$1" || echo 0; }

# /mnt/nfs and /mnt/backups are hard NFS mounts: while TrueNAS hangs, anything that touches them
# blocks forever (and only KILL ends it). So the NAS is probed once, first, and every access below
# is skipped when it does not answer, or time-boxed when it does.
nas_up=false
timeout 5 bash -c "</dev/tcp/$NAS_IP/2049" 2>/dev/null && nas_up=true
# probe <dir>: "ok" (lists entries), "empty", or "hung" (no answer in 45 s; the automount's own
# mount timeout is 30 s, so a slow mount is not cut short).
probe() { local o rc; o=$(timeout -s KILL 45 ls -A "$1" 2>/dev/null); rc=$?; if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then echo hung; elif [ -n "$o" ]; then echo ok; else echo empty; fi; }
nfs_state=skipped; bk_state=skipped
if $nas_up; then
  [ "$NFS_EXPECTED" = true ] && nfs_state=$(probe /mnt/nfs)
  bk_state=$(probe /mnt/backups)
fi

# ---------- host ----------
host_json() {
  local up load1 load5 load15 cores mem_total mem_avail swap_total swap_free
  up=$(cut -d. -f1 /proc/uptime)
  read -r load1 load5 load15 _ < /proc/loadavg
  cores=$(nproc)
  mem_total=$(awk '/MemTotal/ {print $2*1024}' /proc/meminfo)
  mem_avail=$(awk '/MemAvailable/ {print $2*1024}' /proc/meminfo)
  swap_total=$(awk '/SwapTotal/ {print $2*1024}' /proc/meminfo)
  swap_free=$(awk '/SwapFree/ {print $2*1024}' /proc/meminfo)
  # Pages swapped in/out since boot: n8n compares two reports to tell real pressure from leftovers.
  local pswpin pswpout
  pswpin=$(awk '$1=="pswpin" {print $2}' /proc/vmstat); pswpout=$(awk '$1=="pswpout" {print $2}' /proc/vmstat)
  local reboot_required=false pending_updates=0 clock_offset=null
  [ -f /var/run/reboot-required ] && reboot_required=true
  pending_updates=$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst ' || true)
  # chrony/systemd-timesyncd: offset in seconds when known.
  if command -v timedatectl >/dev/null; then
    local synced; synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null)
    [ "$synced" = yes ] && clock_offset=0
  fi
  j -n --argjson up "$up" --argjson l1 "$(num "$load1")" --argjson l5 "$(num "$load5")" \
    --argjson l15 "$(num "$load15")" --argjson cores "$cores" \
    --argjson mt "$mem_total" --argjson ma "$mem_avail" --argjson st "$swap_total" --argjson sf "$swap_free" \
    --argjson rr "$reboot_required" --argjson pu "$(num "$pending_updates")" --argjson co "$clock_offset" \
    --argjson si "$(num "$pswpin")" --argjson so "$(num "$pswpout")" \
    '{uptime_s:$up, load:[$l1,$l5,$l15], cores:$cores,
      mem:{total:$mt, available:$ma, used_pct:(if $mt>0 then (100*($mt-$ma)/$mt|floor) else 0 end)},
      swap:{total:$st, used:($st-$sf), pswpin:$si, pswpout:$so},
      reboot_required:$rr, pending_updates:$pu, clock_synced:($co==0)}'
}

# ---------- kernel: crashed since boot? processes stuck in uninterruptible I/O? ----------
# 2026-10-23 08:38 the gpu kernel hit a BUG in NFS writeback ("list_del corruption"). A Tdarr copy
# to the NAS and, a day later, a Plex scan that touched the same file froze in D state for good;
# nothing can kill them and only a VM restart clears it. Taint bit 128 (D) = an oops since boot.
# stuck: D-state processes (pid, start time, name); n8n flags the ones it saw in the last report too.
kernel_json() {
  local tainted oops stuck
  tainted=$(cat /proc/sys/kernel/tainted 2>/dev/null || echo 0)
  oops=$(journalctl -k -b --no-pager -o short-iso 2>/dev/null \
    | grep -E 'kernel BUG|Oops|list_(add|del) corruption|general protection fault|blocked for more than [0-9]+ seconds' \
    | grep -v 'traps:' | tail -1 | cut -c1-200)   # traps: = a user program's crash, not the kernel's
  # Five samples a second apart, and only processes in D in all five: busy I/O (jbd2 committing the
  # journal, a big copy) dips into D for moments; a stuck one never leaves it (jbd2/sda1 raised a
  # false stuck-io on 2026-10-26 when it was in D at two report times 15 minutes apart).
  stuck=$(for _ in 1 2 3 4 5; do ps -eo pid=,stat=,lstart=,comm= 2>/dev/null; sleep 1; done \
    | awk '$2 ~ /^D/ {pid=$1; $1=$2=""; sub(/^ +/,""); n[pid "\t" $0]++} END {for (k in n) if (n[k] == 5) print k}' \
    | jq -R -s -c 'split("\n") | map(select(length>0) | split("\t") | {pid:(.[0]|tonumber), what:.[1]}) | .[0:20]')
  j -n --argjson t "$(num "$tainted")" --arg o "$oops" --argjson s "${stuck:-[]}" \
    '{tainted:$t, oopsed:(($t / 128 | floor) % 2 == 1), last_oops:(if $o=="" then null else $o end), stuck:$s}'
}

# ---------- disks and mounts ----------
disks_json() {
  # An extra disk that is not mounted would show up as the disk under it: report it as missing.
  local present=() d
  for d in $EXTRA_DISKS; do
    if mountpoint -q "$d"; then present+=("$d"); else printf '{"mount":"%s","missing":true}\n' "$d"; fi
  done
  df -B1 --output=target,size,used,avail / "$APPDATA" "${present[@]}" 2>/dev/null | tail -n +2 \
    | awk '{printf "{\"mount\":\"%s\",\"size\":%s,\"used\":%s,\"avail\":%s,\"used_pct\":%d}\n", $1,$2,$3,$4, (($3+$4)>0? 100*$3/($3+$4) : 0)}'
}
disks_all() { disks_json | jq -s 'unique_by(.mount)'; }

mounts_json() {
  # findmnt reads /proc only, so it is safe on a hung mount; the probes above did the touching
  # (and triggered the automounts). nonempty and responding are null when the NAS was not asked.
  local nfs_mounted=false bk_mounted=false nfs=null
  if [ "$NFS_EXPECTED" = true ]; then
    findmnt -rn -t nfs,nfs4 --mountpoint /mnt/nfs >/dev/null 2>&1 && nfs_mounted=true
    nfs=$(j -n --argjson m "$nfs_mounted" --arg s "$nfs_state" \
      '{mounted:$m, nonempty:(if $s=="skipped" or $s=="hung" then null else $s=="ok" end), responding:(if $s=="skipped" then null else $s!="hung" end)}')
  fi
  findmnt -rn -t nfs,nfs4 --mountpoint /mnt/backups >/dev/null 2>&1 && bk_mounted=true
  local wait_nas; wait_nas=$(systemctl is-active wait-nas 2>/dev/null); [ -n "$wait_nas" ] || wait_nas=absent
  [ "$wait_nas" = inactive ] && ! systemctl cat wait-nas >/dev/null 2>&1 && wait_nas=absent
  j -n --argjson nfs "$nfs" --argjson b "$bk_mounted" --argjson n "$nas_up" --arg w "$wait_nas" \
    '{nfs:$nfs, backups_mounted:$b, nas_tcp_2049:$n, wait_nas:$w}'
}

# ---------- backups ----------
backup_json() {
  local last_ok=null last_rc=null newest=null newest_bytes=null prev_bytes=null timer_next=null
  local base; base="/mnt/backups/$(hostname)"
  # Last successful run: the newest "backup /mnt/backups/..." line in the journal.
  # homelab-backup logs "<date> <time> backup /mnt/backups/<host>/<day>: <size>" on success.
  local t; t=$(journalctl -u homelab-backup -o short-unix --no-pager -g 'backup /mnt/backups' 2>/dev/null | tail -1 | cut -d. -f1)
  [[ "$t" =~ ^[0-9]+$ ]] && last_ok=$t
  local rc; rc=$(systemctl show homelab-backup.service -p ExecMainStatus --value 2>/dev/null)
  [[ "$rc" =~ ^[0-9]+$ ]] && last_rc=$rc
  local tn; tn=$(systemctl show homelab-backup.timer -p NextElapseUSecRealtime --value 2>/dev/null)
  [ -n "$tn" ] && timer_next=$(date -d "$tn" +%s 2>/dev/null || echo null)
  [[ "$timer_next" =~ ^[0-9]+$ ]] || timer_next=null
  # Only when /mnt/backups answered (see probe), and every call time-boxed.
  if [ "$bk_state" = ok ] && timeout -s KILL 10 test -d "$base"; then
    local dirs; mapfile -t dirs < <(timeout -s KILL 30 find "$base" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9]+-[0-9]+-[0-9]+' 2>/dev/null | sort)
    if [ "${#dirs[@]}" -ge 1 ]; then
      newest=$(basename "${dirs[-1]}"); newest_bytes=$(timeout -s KILL 60 du -sb "${dirs[-1]}" 2>/dev/null | cut -f1)
    fi
    [ "${#dirs[@]}" -ge 2 ] && prev_bytes=$(timeout -s KILL 60 du -sb "${dirs[-2]}" 2>/dev/null | cut -f1)
  fi
  [ "$newest" = null ] && newest=""
  j -n --argjson lo "$last_ok" --argjson rc "$last_rc" --arg n "$newest" \
    --argjson nb "$(num "${newest_bytes:-}")" --argjson pb "$(num "${prev_bytes:-}")" --argjson tn "$timer_next" \
    '{last_success:$lo, last_rc:$rc, newest:(if $n=="" then null else $n end), newest_bytes:$nb, previous_bytes:$pb, timer_next:$tn}'
}

# ---------- docker ----------
docker_json() {
  local containers='[]' missing='[]' reclaim=0 auto='[]' allowed='[]'
  # Services the ops door may restart or stop (/etc/homelab-ops.allow): n8n plans fixes from this
  # list, so a fix it would plan is never one the door then refuses.
  [ -r /etc/homelab-ops.allow ] && allowed=$(awk -F/ '!/^#/ && NF == 2 { print $2 }' /etc/homelab-ops.allow \
    | jq -R -s -c 'split("\n") | map(select(length > 0))')
  # Services the sysadmin may update itself (role ops, hosts.yml auto_update): "stack/service".
  [ -r /etc/homelab-autoupdate.conf ] && auto=$(awk '!/^#/ && NF > 1 { for (i = 2; i <= NF; i++) print $1 "/" $i }' \
    /etc/homelab-autoupdate.conf | jq -R -s -c 'split("\n") | map(select(length > 0))')
  if docker info >/dev/null 2>&1; then
    containers=$(docker ps -a --format '{{.Names}}' | while read -r n; do
      docker inspect "$n" 2>/dev/null | jq -c '.[0] | {
        name:.Name|ltrimstr("/"), id:.Id[0:12], state:.State.Status, health:(.State.Health.Status // "none"),
        restarts:.RestartCount, started_at:.State.StartedAt, exit_code:.State.ExitCode,
        image:.Config.Image, image_id:.Image[7:19], stack:(.Config.Labels["com.docker.compose.project"] // null),
        service:(.Config.Labels["com.docker.compose.service"] // null)}'
    done | jq -s '.')
    # Expected services (from hosts.yml stack_services, or the whole compose file) that are not running.
    local exp_list=()
    for s in $STACKS; do
      local lst
      if [ -n "$EXPECTED" ]; then
        lst=$(tr ' ' '\n' <<<"$EXPECTED" | awk -F/ -v s="$s" '$1==s {print $2}')
      else
        lst=$(docker compose -f "/opt/stacks/$s/compose.yaml" --env-file "/opt/stacks/$s/.env" config --services 2>/dev/null)
      fi
      for svc in $lst; do exp_list+=("$s/$svc"); done
    done
    missing=$(printf '%s\n' "${exp_list[@]:-}" | grep -v '^$' | jq -R -s --argjson c "$containers" '
      split("\n") | map(select(length>0)) | map(. as $e | select(
        ($c | map(select((.stack+"/"+.service)==$e and .state=="running")) | length) == 0))')
    reclaim=$(docker system df --format '{{.Reclaimable}}' 2>/dev/null | head -2 | awk '
      { v=$1; sub(/[^0-9.].*/,"",v); u=$1; sub(/^[0-9.]+/,"",u);
        m=1; if (u ~ /^kB/) m=1e3; if (u ~ /^MB/) m=1e6; if (u ~ /^GB/) m=1e9; if (u ~ /^TB/) m=1e12; t+=v*m }
      END { printf "%d", t }')
  fi
  j -n --argjson c "$containers" --argjson m "$missing" --argjson r "$(num "$reclaim")" --argjson a "$auto" --argjson w "$allowed" \
    '{containers:$c, missing:$m, reclaimable_bytes:$r, auto_update:$a, allowed:$w}'
}

# ---------- media apps (only where their config exists) ----------
arr_key() { sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$APPDATA/$1/config.xml" 2>/dev/null | head -1; }

arr_json() { # name port apiver kind(movie|series|indexer)
  local name=$1 port=$2 v=$3 kind=$4 key; key=$(arr_key "$name")
  [ -z "$key" ] && { echo null; return; }
  local base="http://127.0.0.1:$port/api/$v" h="X-Api-Key: $key"
  local health; health=$(get -H "$h" "$base/health" | jq -c 'if type=="array" then map({type,source,message}) else [] end' 2>/dev/null || echo '[]')
  local queue='[]' counts='{}'
  if [ "$kind" != indexer ]; then
    local q; q=$(get -H "$h" "$base/queue?pageSize=500&includeUnknown${kind^}Items=true")
    queue=$(jq -c --argjson now "$now" '(.records // []) | map({
        id, title, status, tds:.trackedDownloadStatus, tdstate:.trackedDownloadState,
        protocol, size, sizeleft, added,
        age_h:(if .added then ((($now - (.added|sub("\\.[0-9]+";"")|fromdate)) / 3600)|floor) else null end),
        messages:((.statusMessages // []) | map(.messages // []) | flatten | .[0:3]),
        ref:(.movieId // .seriesId // null)})
      | map(select(.tds=="warning" or .tds=="error" or .tdstate=="importBlocked" or .tdstate=="importPending"
                   or .status=="failed" or .status=="warning" or (.age_h != null and .age_h > 24)))
      | .[0:50]' <<<"$q" 2>/dev/null || echo '[]')
    counts=$(jq -c '{total:(.totalRecords // 0)}' <<<"$q" 2>/dev/null || echo '{}')
  else
    # Prowlarr: indexers disabled by backoff.
    local st ix; st=$(get -H "$h" "$base/indexerstatus"); ix=$(get -H "$h" "$base/indexer")
    queue=$(printf '%s\n%s\n' "$st" "$ix" | jq -c -s '.[0] as $st | .[1] as $ix |
      ($ix | if type=="array" then map({key:(.id|tostring), value:.name}) | from_entries else {} end) as $names
      | ($st | if type=="array" then . else [] end)
      | map(select(.disabledTill != null) | {indexer_id:.indexerId, name:($names[(.indexerId|tostring)] // "?"),
             disabled_till:.disabledTill, failures:.mostRecentFailure})' 2>/dev/null || echo '[]')
  fi
  j -n --argjson h "$health" --argjson q "$queue" --argjson c "$counts" '{health:$h, stuck:$q, queue:$c}'
}

sab_json() {
  local ini="$APPDATA/sabnzbd/sabnzbd.ini" key
  key=$(sed -n 's/^api_key *= *//p' "$ini" 2>/dev/null | head -1)
  [ -z "$key" ] && { echo null; return; }
  local base="http://127.0.0.1:8080/api?output=json&apikey=$key"
  local q h; q=$(get "$base&mode=queue&limit=5"); h=$(get "$base&mode=history&limit=100")
  # Both documents go in on stdin: a --argjson value counts against the kernel's single-argument limit.
  printf '%s\n%s\n' "$q" "$h" | jq -c -s --argjson now "$now" '.[0] as $q | .[1] as $h |
    {status:($q.queue.status // null), paused:($q.queue.paused // null), pause_int:($q.queue.pause_int // null),
     diskspace_gb:(($q.queue.diskspace1 // "0")|tonumber? // 0), queue_count:(($q.queue.noofslots_total // 0)|tonumber? // 0),
     speed:($q.queue.speed // null),
     failed_24h:(($h.history.slots // []) | map(select(.status=="Failed" and ((.completed // 0) > ($now - 86400)))) | map({name, fail_message}) | .[0:20])}' 2>/dev/null || echo null
}

qb_json() {
  # qBittorrent shares gluetun's network namespace and skips its login for localhost there.
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^media-gluetun-1$' || { echo null; return; }
  local ip prefs qb_api=false listen pf torrents
  ip=$(docker exec media-gluetun-1 wget -qO- -T 10 https://api.ipify.org 2>/dev/null || true)
  # The port PIA forwarded is gluetun's (its status file); qBittorrent's listen_port is only what
  # gluetun last handed over, and stays set when forwarding is lost.
  pf=$(docker exec media-gluetun-1 cat /tmp/gluetun/forwarded_port 2>/dev/null | tr -dc '0-9')
  prefs=$(docker exec media-gluetun-1 wget -qO- -T 10 http://127.0.0.1:8085/api/v2/app/preferences 2>/dev/null)
  listen=$(jq -r '.listen_port // empty' <<<"$prefs" 2>/dev/null) && [ -n "$listen" ] && qb_api=true
  torrents=$(docker exec media-gluetun-1 wget -qO- -T 10 'http://127.0.0.1:8085/api/v2/torrents/info' 2>/dev/null || echo '[]')
  local home_ip; home_ip=$(curl -fsS -m 10 https://api.ipify.org 2>/dev/null || true)
  # The torrent list grows past the kernel's single-argument limit (jq: "Argument list too long",
  # seen on media 2026-10-22, which silenced the whole report), so it arrives on stdin.
  jq -c --arg ip "$ip" --arg home "$home_ip" --arg pf "${pf:-}" --arg lp "${listen:-}" --argjson api "$qb_api" --argjson now "$now" '. as $t |
    {exit_ip:(if $ip=="" then null else $ip end), home_ip:(if $home=="" then null else $home end),
     vpn_ok:($ip != "" and $ip != $home), forwarded_port:(if $pf=="" then null else ($pf|tonumber) end),
     listen_port:(if $lp=="" then null else ($lp|tonumber) end), qb_api:$api,
     count:($t|length),
     errored:($t | map(select(.state=="error" or .state=="missingFiles")) | map({name, state}) | .[0:20]),
     stalled_3d:($t | map(select((.state=="stalledDL" or .state=="metaDL") and (.added_on < ($now - 259200)))) | map({name, state, progress}) | .[0:20])}' \
    <<<"$torrents" 2>/dev/null || echo null
}

tdarr_json() {
  [ -d "$APPDATA/tdarr" ] || { echo null; return; }
  local base="http://127.0.0.1:8266/api/v2"
  local stats nodes key=()
  # Tdarr's API login is on: the same key tdarr_setup.py uses (root-only file on the gpu VM).
  [ -r /root/tdarr-api-key ] && key=(-H "x-api-key: $(tr -d '[:space:]' </root/tdarr-api-key)")
  stats=$(get "${key[@]}" -H 'Content-Type: application/json' -X POST "$base/cruddb" \
    -d '{"data":{"collection":"StatisticsJSONDB","mode":"getById","docID":"statistics"}}')
  nodes=$(get "${key[@]}" "$base/get-nodes")
  printf '%s\n%s\n' "$stats" "$nodes" | jq -c -s '.[0] as $s | .[1] as $n |
    {queued:($s.table1Count // null), done:($s.table2Count // null), errored:($s.table3Count // null),
     health_queued:($s.table4Count // null), total_files:($s.totalFileCount // null),
     space_saved_gb:($s.sizeDiff // null),
     nodes:($n | if type=="object" then [to_entries[] | {name:.value.nodeName, paused:.value.nodePaused,
            workers:((.value.workers // {}) | length)}] else [] end)}' 2>/dev/null || echo null
}

media_json() {
  local sonarr radarr prowlarr sab qb tdarr
  sonarr=$(arr_json sonarr 8989 v3 series)
  radarr=$(arr_json radarr 7878 v3 movie)
  prowlarr=$(arr_json prowlarr 9696 v1 indexer)
  sab=$(sab_json); qb=$(qb_json); tdarr=$(tdarr_json)
  j -n --argjson s "$sonarr" --argjson r "$radarr" --argjson p "$prowlarr" --argjson sab "$sab" --argjson qb "$qb" --argjson t "$tdarr" \
    '{sonarr:$s, radarr:$r, prowlarr:$p, sabnzbd:$sab, qbittorrent:$qb, tdarr:$t} | with_entries(select(.value != null))'
}

# ---------- Plex: can apps outside the LAN find it? ----------
# 2026-10-25: Plex answered on the LAN for days while its Remote Access switch was off; plex.tv
# refused its check-ins (422) and then stopped listing it as a server, so the Roku and every app
# that finds servers through plex.tv showed nothing. The health check and Kuma stayed green.
# The token is Plex's own (Preferences.xml); it goes only to Plex on localhost and to plex.tv,
# which issued it.
plex_json() {
  local prefs="$APPDATA/plex/Library/Application Support/Plex Media Server/Preferences.xml"
  [ -r "$prefs" ] || { echo null; return; }
  local tok ident acct pset res
  tok=$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$prefs" | head -1)
  ident=$(get -H 'Accept: application/json' http://127.0.0.1:32400/identity)
  if [ -n "$tok" ]; then
    local h=(-H 'Accept: application/json' -H "X-Plex-Token: $tok")
    acct=$(get "${h[@]}" http://127.0.0.1:32400/myplex/account)
    pset=$(get "${h[@]}" 'http://127.0.0.1:32400/:/prefs')
    res=$(get "${h[@]}" -H 'X-Plex-Client-Identifier: homelab-agent' 'https://plex.tv/api/v2/resources')
  else acct='{}'; pset='{}'; res='{}'; fi
  # Devices Plex turned away (401) in the last 15 minutes, by address. A device that holds a
  # sign-in plex.tv no longer issues (the Roku, 2026-10-25) keeps knocking and gets nothing.
  local logf="${prefs%/Preferences.xml}/Logs/Plex Media Server.log" refused='[]'
  if [ -r "$logf" ]; then
    refused=$(tail -n 200000 "$logf" | awk -v since="$(date -d '15 min ago' '+%Y%m%d%H%M%S')" '
      BEGIN { split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", m, " "); for (i in m) mon[m[i]] = sprintf("%02d", i) }
      /Completed: \[/ && / 401 / {
        d = $2; sub(",", "", d); t = $4; gsub(/[:.]/, "", t)
        if ($3 mon[$1] sprintf("%02d", d) substr(t, 1, 6) < since) next
        ip = $0; sub(/.*Completed: \[/, "", ip); sub(/\].*/, "", ip); sub(/:[0-9]+$/, "", ip); n[ip]++ }
      END { for (ip in n) printf "%s\t%d\n", ip, n[ip] }' \
      | jq -R -s -c 'split("\n") | map(select(length>0) | split("\t") | {ip:.[0], n:(.[1]|tonumber)}) | sort_by(-.n) | .[0:5]')
  fi
  # listed: plex.tv has this server (by machine id) as one that provides "server"; null when
  # plex.tv or Plex did not answer.
  printf '%s\n%s\n%s\n%s\n' "$ident" "$acct" "$pset" "$res" | jq -c -s --argjson tok "$([ -n "$tok" ] && echo true || echo false)" --argjson refused "${refused:-[]}" '
    .[0].MediaContainer.machineIdentifier as $id | (.[1].MyPlex // {}) as $a
    | ((.[2].MediaContainer.Setting // []) | map(select(.id == "PublishServerOnPlexOnlineKey")) | .[0].value) as $pub
    | {answering:($id != null), token:$tok,
       publish:(if $pub == null then null else ($pub == true or $pub == "1" or $pub == 1) end),
       sign_in:($a.signInState // null), mapping:($a.mappingState // null),
       mapping_error:(if ($a.mappingError // "") == "" then null else $a.mappingError end),
       public_port:($a.publicPort // null), refused:$refused,
       listed:(if (.[3] | type) == "array" and $id != null
               then (.[3] | map(select(.clientIdentifier == $id and ((.provides // "") | split(",") | index("server")))) | length > 0)
               else null end)}'
}

# ---------- logins: are the app credentials still what they were? ----------
# Fingerprints only (sha256, first 16 hex chars); no password, hash or API key leaves the VM.
# n8n compares each run with the previous one and emails when a login or key changes, when the
# app rejects its own key, when a consumer (Prowlarr, Bazarr, Recyclarr, Jellyseerr, the stack
# .env, Homepage) holds a different key than the app, and when logins fail (2026-10-22: a
# password mismatch sat unnoticed for hours).
fp() { printf '%s' "$1" | sha256sum | cut -c1-16; }
arr_logins() { # name port apiver
  local name=$1 port=$2 v=$3
  local cfg="$APPDATA/$name/config.xml"
  [ -r "$cfg" ] || { echo null; return; }
  local method required key users=null code=null fails=0 last_fail=null
  method=$(sed -n 's:.*<AuthenticationMethod>\(.*\)</AuthenticationMethod>.*:\1:p' "$cfg" | head -1)
  required=$(sed -n 's:.*<AuthenticationRequired>\(.*\)</AuthenticationRequired>.*:\1:p' "$cfg" | head -1)
  key=$(arr_key "$name")
  local db="$APPDATA/$name/$name.db"
  if [ -r "$db" ] && command -v sqlite3 >/dev/null; then
    # A locked database (the app is writing) must read as "unknown", not as "no users".
    local rows
    if rows=$(sqlite3 -readonly -cmd '.timeout 3000' "$db" "select Username||'|'||Password||'|'||ifnull(Salt,'') from Users order by Id" 2>/dev/null); then
      users=$(jq -R -s -c 'split("\n") | map(select(length>0))' <<<"$rows" 2>/dev/null)
      [ -n "$users" ] && users=$(jq -c --arg fp "$(fp "$users")" '{count:length, fp:$fp, names:map(split("|")[0])}' <<<"$users") || users=null
    fi
  fi
  [ -n "$key" ] && code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' -H "X-Api-Key: $key" "http://127.0.0.1:$port/api/$v/system/status" 2>/dev/null)
  [[ "$code" =~ ^[1-9][0-9]+$ ]] || code=null # 000 = no answer
  # Failed form logins in the current log file (the app logs "Auth-Failure" with the username).
  local logf="$APPDATA/$name/logs/$name.txt"
  if [ -r "$logf" ]; then
    fails=$(grep -c 'Auth-Failure' "$logf" 2>/dev/null || true)
    last_fail=$(grep 'Auth-Failure' "$logf" 2>/dev/null | tail -1 | cut -c1-19)
  fi
  j -n --arg m "$method" --arg r "$required" --arg k "$(fp "$key")" --argjson u "$users" --argjson c "$code" \
    --argjson f "$(num "$fails")" --arg lf "${last_fail:-}" \
    '{auth_method:$m, auth_required:$r, apikey_fp:(if $k=="" then null else $k end), users:$u, api_http:$c,
      auth_failures:$f, last_failure:(if $lf=="" then null else $lf end)}'
}

# Where other apps keep Sonarr's and Radarr's keys: fingerprint each so n8n can spot a mismatch.
consumer_keys() {
  local out='{}' k
  # Prowlarr > Settings > Apps (settings JSON holds "apiKey").
  if [ -r "$APPDATA/prowlarr/prowlarr.db" ] && command -v sqlite3 >/dev/null; then
    while IFS='|' read -r impl key; do
      [ -n "$key" ] && out=$(jq -c --arg a "prowlarr:${impl,,}" --arg f "$(fp "$key")" '.[$a]=$f' <<<"$out")
    done < <(sqlite3 -readonly "$APPDATA/prowlarr/prowlarr.db" "select Implementation, json_extract(Settings,'$.apiKey') from Applications" 2>/dev/null)
  fi
  # Bazarr config.yaml: a sonarr: / radarr: block with an apikey: line.
  local bz="$APPDATA/bazarr/config/config.yaml"
  if [ -r "$bz" ]; then
    for app in sonarr radarr; do
      k=$(awk -v app="$app" '$0 ~ "^"app":" {s=1; next} s && /^[^ ]/ {s=0} s && /^ *apikey:/ {gsub(/[ "'"'"']/,"",$2); print $2; exit}' "$bz")
      [ -n "$k" ] && out=$(jq -c --arg a "bazarr:$app" --arg f "$(fp "$k")" '.[$a]=$f' <<<"$out")
    done
  fi
  # The media stack .env (Recyclarr and the profile sorter read SONARR_API_KEY/RADARR_API_KEY).
  for app in sonarr radarr; do
    k=$(sed -n "s/^${app^^}_API_KEY=//p" /opt/stacks/media/.env 2>/dev/null | tr -d '"' | head -1)
    [ -n "$k" ] && out=$(jq -c --arg a "env:$app" --arg f "$(fp "$k")" '.[$a]=$f' <<<"$out")
  done
  # Jellyseerr settings.json.
  local js="$APPDATA/jellyseerr/settings.json"
  if [ -r "$js" ]; then
    for app in sonarr radarr; do
      k=$(jq -r ".${app}[0].apiKey // empty" "$js" 2>/dev/null)
      [ -n "$k" ] && out=$(jq -c --arg a "jellyseerr:$app" --arg f "$(fp "$k")" '.[$a]=$f' <<<"$out")
    done
  fi
  # Homepage (apps VM): HOMEPAGE_<APP>_KEY in the apps stack .env.
  for app in sonarr radarr prowlarr bazarr sabnzbd; do
    k=$(sed -n "s/^HOMEPAGE_${app^^}_KEY=//p" /opt/stacks/apps/.env 2>/dev/null | tr -d '"' | head -1)
    [ -n "$k" ] && out=$(jq -c --arg a "homepage:$app" --arg f "$(fp "$k")" '.[$a]=$f' <<<"$out")
  done
  echo "$out"
}

sab_logins() {
  local ini="$APPDATA/sabnzbd/sabnzbd.ini"
  [ -r "$ini" ] || { echo null; return; }
  local user pass key code=null
  user=$(sed -n 's/^username *= *//p' "$ini" | head -1); pass=$(sed -n 's/^password *= *//p' "$ini" | head -1)
  key=$(sed -n 's/^api_key *= *//p' "$ini" | head -1)
  [ -n "$key" ] && code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "http://127.0.0.1:8080/api?mode=version&apikey=$key" 2>/dev/null)
  [[ "$code" =~ ^[1-9][0-9]+$ ]] || code=null
  j -n --arg u "$user" --arg lp "$(fp "$user|$pass")" --arg k "$(fp "$key")" --argjson c "$code" \
    '{login_set:($u!=""), login_fp:$lp, apikey_fp:(if $k=="" then null else $k end), api_http:$c}'
}

logins_json() {
  local sonarr radarr prowlarr sab consumers
  sonarr=$(arr_logins sonarr 8989 v3); radarr=$(arr_logins radarr 7878 v3); prowlarr=$(arr_logins prowlarr 9696 v1)
  sab=$(sab_logins); consumers=$(consumer_keys)
  j -n --argjson s "$sonarr" --argjson r "$radarr" --argjson p "$prowlarr" --argjson sab "$sab" --argjson c "$consumers" \
    '{sonarr:$s, radarr:$r, prowlarr:$p, sabnzbd:$sab, consumers:$c} | with_entries(select(.value != null and .value != {}))'
}

# ---------- edge: Tailscale and certificates ----------
edge_json() {
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^edge-tailscale-1$' || { echo null; return; }
  local ts; ts=$(docker exec edge-tailscale-1 tailscale status --json 2>/dev/null || echo '{}')
  local tsj; tsj=$(jq -c --argjson now "$now" '{
      online:(.Self.Online // false), key_expiry:(.Self.KeyExpiry // null),
      key_days_left:(if .Self.KeyExpiry then (((.Self.KeyExpiry|sub("\\.[0-9]+";"")|fromdate) - $now)/86400|floor) else null end),
      routes:(.Self.PrimaryRoutes // []), peers_online:([.Peer[]? | select(.Online)] | length)}' <<<"$ts" 2>/dev/null || echo '{}')
  # One TLS handshake per ts.net name that TSDProxy publishes (apps.yaml keys). The VM's own
  # resolver knows nothing about MagicDNS, so each name is resolved through the tailscale
  # container (the TSDProxy nodes are its peers) and the handshake goes to that IP with SNI.
  local tailnet; tailnet=$(sed -n 's/^TS_TAILNET=//p' /opt/stacks/edge/.env 2>/dev/null | tr -d '"')
  local certs='[]'
  if [ -n "$tailnet" ] && [ -r /opt/stacks/edge/tsdproxy/apps.yaml ]; then
    certs=$(grep -E '^[a-z0-9-]+:$' /opt/stacks/edge/tsdproxy/apps.yaml | tr -d ':' | while read -r app; do
      [[ " $CERT_SKIP " == *" $app "* ]] && continue
      # Every name starts clean: a second "local exp" in the same function keeps the previous name's value.
      local host="$app.$tailnet" exp="" days=null ok=false ip="" why=""
      ip=$(jq -r --arg h "$host." '[.Peer[]? | select(.DNSName == $h and .Online) | .TailscaleIPs[0]] | first // empty' <<<"$ts")
      [ -n "$ip" ] || ip=$(timeout 10 docker exec edge-tailscale-1 nslookup "$host" 100.100.100.100 2>/dev/null | awk '/^Address/ && $2 !~ /100\.100\.100\.100/ {print $2; exit}')
      if [ -z "$ip" ]; then why="not on the tailnet"; else
        exp=$(timeout 15 openssl s_client -servername "$host" -connect "$ip:443" </dev/null 2>/dev/null \
              | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
        [ -n "$exp" ] || why="no TLS answer"
      fi
      if [ -n "${exp:-}" ]; then ok=true; days=$(( ($(date -d "$exp" +%s) - now) / 86400 )); else days=null; fi
      jq -c -n --arg h "$host" --argjson ok "$ok" --argjson d "$days" --arg w "$why" '{host:$h, ok:$ok, days_left:$d, why:(if $w=="" then null else $w end)}'
    done | jq -s '.')
  fi
  j -n --argjson t "$tsj" --argjson c "$certs" '{tailscale:$t, certs:$c}'
}

# ---------- assemble ----------
# Each section goes to a file of its own. One that is not a single valid JSON document becomes null
# and its name lands in "errors", so one bad section never blanks the whole report; the files go
# into jq with --slurpfile, so no section counts against the kernel's argument-size limit.
# The files live on /run (tmpfs): a full root disk must not stop the report that says so.
tmp=$(mktemp -d -p /run homelab-agent.XXXXXX 2>/dev/null || mktemp -d 2>/dev/null) || tmp=""
trap '[ -n "$tmp" ] && rm -rf "$tmp"' EXIT
errors=()
section() { # name function
  local name=$1; shift
  "$@" >"$tmp/$name.json" 2>"$tmp/$name.err"
  if ! { [ -s "$tmp/$name.json" ] && jq -e 'true' "$tmp/$name.json" >/dev/null 2>&1; }; then
    errors+=("$name: $(tail -1 "$tmp/$name.err" | cut -c1-200)")
    echo null >"$tmp/$name.json"
  fi
}
why="no writable temp folder"
if [ -n "$tmp" ]; then
  section system host_json; section disks disks_all; section mounts mounts_json; section backup backup_json
  section docker docker_json; section media media_json; section edge edge_json; section logins logins_json
  section plex plex_json; section kernel kernel_json
  jq -n '$ARGS.positional' --args "${errors[@]}" >"$tmp/errors.json"
  jq -c -n --arg host "$(hostname)" --argjson ts "$now" --argjson v "$AGENT_VERSION" \
    --slurpfile system "$tmp/system.json" --slurpfile disks "$tmp/disks.json" --slurpfile mounts "$tmp/mounts.json" \
    --slurpfile backup "$tmp/backup.json" --slurpfile docker "$tmp/docker.json" --slurpfile media "$tmp/media.json" \
    --slurpfile edge "$tmp/edge.json" --slurpfile logins "$tmp/logins.json" --slurpfile plex "$tmp/plex.json" --slurpfile kernel "$tmp/kernel.json" \
    --slurpfile errors "$tmp/errors.json" \
    '{host:$host, ts:$ts, agent_version:$v, system:$system[0], disks:$disks[0], mounts:$mounts[0], backup:$backup[0],
      docker:$docker[0], media:$media[0], logins:$logins[0], edge:$edge[0], plex:$plex[0], kernel:$kernel[0], errors:$errors[0]}' >"$tmp/report.json" 2>"$tmp/report.err"
  why=$(tail -1 "$tmp/report.err" 2>/dev/null | cut -c1-200)
fi
if [ -n "$tmp" ] && jq -e 'type == "object"' "$tmp/report.json" >/dev/null 2>&1; then
  data=@"$tmp/report.json"
else
  # Still say something, built in memory: n8n raises "could not build its report" instead of
  # waiting 40 minutes for silence.
  logger -t homelab-agent "report assembly failed: ${why:-unknown}"
  data=$(jq -c -n --arg host "$(hostname)" --argjson ts "$now" --argjson v "$AGENT_VERSION" \
    --arg e "report assembly failed: ${why:-unknown}; $(df -h --output=avail / 2>/dev/null | tail -1 | tr -d ' ') free on /" \
    '{host:$host, ts:$ts, agent_version:$v, agent_error:$e}')
fi

if [ "${1:-}" = "--print" ]; then
  if [ "${data:0:1}" = "@" ]; then jq . "${data:1}"; else jq . <<<"$data"; fi
else
  curl -fsS -m 20 -X POST -H 'Content-Type: application/json' --data-binary "$data" "$REPORT_URL" >/dev/null \
    || logger -t homelab-agent "could not deliver report to $REPORT_URL"
fi
