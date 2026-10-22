#!/bin/bash
# Reporter for the n8n "Homelab sysadmin" workflow. Collects this VM's facts every 15 minutes
# and POSTs one JSON document to n8n. Read-only: nothing here restarts, deletes or changes
# anything. App API keys are read from the app's own config under /opt/appdata and used only
# against localhost; they never leave this VM.
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
EXTRA_DISKS=${EXTRA_DISKS:-}           # more mount points to report, e.g. the SABnzbd scratch disk
APPDATA=/opt/appdata
AGENT_VERSION=2

now=$(date +%s)
j() { jq -c "$@"; }
# curl with short timeouts; prints {} on any failure so jq downstream never breaks.
get() { curl -fsS -m 15 "$@" 2>/dev/null || echo '{}'; }
num() { [[ "${1:-}" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && echo "$1" || echo 0; }

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
    '{uptime_s:$up, load:[$l1,$l5,$l15], cores:$cores,
      mem:{total:$mt, available:$ma, used_pct:(if $mt>0 then (100*($mt-$ma)/$mt|floor) else 0 end)},
      swap:{total:$st, used:($st-$sf)},
      reboot_required:$rr, pending_updates:$pu, clock_synced:($co==0)}'
}

# ---------- disks and mounts ----------
disks_json() {
  # shellcheck disable=SC2086
  df -B1 --output=target,size,used,avail / "$APPDATA" $EXTRA_DISKS 2>/dev/null | tail -n +2 \
    | awk '{printf "{\"mount\":\"%s\",\"size\":%s,\"used\":%s,\"avail\":%s,\"used_pct\":%d}\n", $1,$2,$3,$4, ($2>0? 100*$3/$2 : 0)}' \
    | jq -s 'unique_by(.mount)'
}

mounts_json() {
  local nfs_mounted=false nfs_nonempty=false bk_mounted=false nas_up=false
  ls /mnt/nfs >/dev/null 2>&1 # triggers the automount
  findmnt -rn -t nfs,nfs4 --mountpoint /mnt/nfs >/dev/null 2>&1 && nfs_mounted=true
  [ -n "$(ls -A /mnt/nfs 2>/dev/null)" ] && nfs_nonempty=true
  ls /mnt/backups >/dev/null 2>&1
  findmnt -rn -t nfs,nfs4 --mountpoint /mnt/backups >/dev/null 2>&1 && bk_mounted=true
  timeout 5 bash -c "</dev/tcp/$NAS_IP/2049" 2>/dev/null && nas_up=true
  local wait_nas; wait_nas=$(systemctl is-active wait-nas 2>/dev/null); [ -n "$wait_nas" ] || wait_nas=absent
  [ "$wait_nas" = inactive ] && ! systemctl cat wait-nas >/dev/null 2>&1 && wait_nas=absent
  j -n --argjson m "$nfs_mounted" --argjson e "$nfs_nonempty" --argjson b "$bk_mounted" \
    --argjson n "$nas_up" --arg w "$wait_nas" \
    '{nfs:{mounted:$m, nonempty:$e}, backups_mounted:$b, nas_tcp_2049:$n, wait_nas:$w}'
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
  if [ -d "$base" ]; then
    local dirs; mapfile -t dirs < <(find "$base" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9]+-[0-9]+-[0-9]+' 2>/dev/null | sort)
    if [ "${#dirs[@]}" -ge 1 ]; then
      newest=$(basename "${dirs[-1]}"); newest_bytes=$(du -sb "${dirs[-1]}" 2>/dev/null | cut -f1)
    fi
    [ "${#dirs[@]}" -ge 2 ] && prev_bytes=$(du -sb "${dirs[-2]}" 2>/dev/null | cut -f1)
  fi
  [ "$newest" = null ] && newest=""
  j -n --argjson lo "$last_ok" --argjson rc "$last_rc" --arg n "$newest" \
    --argjson nb "$(num "${newest_bytes:-}")" --argjson pb "$(num "${prev_bytes:-}")" --argjson tn "$timer_next" \
    '{last_success:$lo, last_rc:$rc, newest:(if $n=="" then null else $n end), newest_bytes:$nb, previous_bytes:$pb, timer_next:$tn}'
}

# ---------- docker ----------
docker_json() {
  local containers='[]' missing='[]' reclaim=0
  if docker info >/dev/null 2>&1; then
    containers=$(docker ps -a --format '{{.Names}}' | while read -r n; do
      docker inspect "$n" 2>/dev/null | jq -c '.[0] | {
        name:.Name|ltrimstr("/"), state:.State.Status, health:(.State.Health.Status // "none"),
        restarts:.RestartCount, started_at:.State.StartedAt, exit_code:.State.ExitCode,
        image:.Config.Image, stack:(.Config.Labels["com.docker.compose.project"] // null),
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
  j -n --argjson c "$containers" --argjson m "$missing" --argjson r "$(num "$reclaim")" \
    '{containers:$c, missing:$m, reclaimable_bytes:$r}'
}

# ---------- media apps (only where their config exists) ----------
arr_key() { sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$APPDATA/$1/config.xml" 2>/dev/null | head -1; }

arr_json() { # name port apiver kind(movie|series|indexer)
  local name=$1 port=$2 v=$3 kind=$4 key; key=$(arr_key "$name")
  [ -z "$key" ] && { echo null; return; }
  local base="http://127.0.0.1:$port/api/$v" h="X-Api-Key: $key"
  local health; health=$(get -H "$h" "$base/health" | jq -c 'if type=="array" then map({type,source,message}) else [] end')
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
    queue=$(jq -c -n --argjson st "$st" --argjson ix "$ix" '
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
  jq -c -n --argjson q "$q" --argjson h "$h" --argjson now "$now" '
    {status:($q.queue.status // null), paused:($q.queue.paused // null), pause_int:($q.queue.pause_int // null),
     diskspace_gb:(($q.queue.diskspace1 // "0")|tonumber? // 0), queue_count:(($q.queue.noofslots_total // 0)|tonumber? // 0),
     speed:($q.queue.speed // null),
     failed_24h:(($h.history.slots // []) | map(select(.status=="Failed" and ((.completed // 0) > ($now - 86400)))) | map({name, fail_message}) | .[0:20])}'
}

qb_json() {
  # qBittorrent shares gluetun's network namespace and skips its login for localhost there.
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^media-gluetun-1$' || { echo null; return; }
  local ip port torrents
  ip=$(docker exec media-gluetun-1 wget -qO- -T 10 https://api.ipify.org 2>/dev/null || true)
  port=$(docker exec media-gluetun-1 wget -qO- -T 5 http://127.0.0.1:8085/api/v2/app/preferences 2>/dev/null | jq -r '.listen_port // empty' 2>/dev/null)
  torrents=$(docker exec media-gluetun-1 wget -qO- -T 10 'http://127.0.0.1:8085/api/v2/torrents/info' 2>/dev/null || echo '[]')
  local home_ip; home_ip=$(curl -fsS -m 10 https://api.ipify.org 2>/dev/null || true)
  jq -c -n --arg ip "$ip" --arg home "$home_ip" --arg port "${port:-}" --argjson t "$torrents" --argjson now "$now" '
    {exit_ip:(if $ip=="" then null else $ip end), home_ip:(if $home=="" then null else $home end),
     vpn_ok:($ip != "" and $ip != $home), forwarded_port:(if $port=="" then null else ($port|tonumber) end),
     count:($t|length),
     errored:($t | map(select(.state=="error" or .state=="missingFiles")) | map({name, state}) | .[0:20]),
     stalled_3d:($t | map(select((.state=="stalledDL" or .state=="metaDL") and (.added_on < ($now - 259200)))) | map({name, state, progress}) | .[0:20])}'
}

tdarr_json() {
  [ -d "$APPDATA/tdarr" ] || { echo null; return; }
  local base="http://127.0.0.1:8266/api/v2"
  local stats nodes
  stats=$(curl -fsS -m 15 -H 'Content-Type: application/json' -X POST "$base/cruddb" \
    -d '{"data":{"collection":"StatisticsJSONDB","mode":"getById","docID":"statistics"}}' 2>/dev/null || echo '{}')
  nodes=$(curl -fsS -m 15 "$base/get-nodes" 2>/dev/null || echo '{}')
  jq -c -n --argjson s "$stats" --argjson n "$nodes" '
    {queued:($s.table1Count // null), done:($s.table2Count // null), errored:($s.table3Count // null),
     health_queued:($s.table4Count // null), total_files:($s.totalFileCount // null),
     space_saved_gb:($s.sizeDiff // null),
     nodes:($n | if type=="object" then [to_entries[] | {name:.value.nodeName, paused:.value.nodePaused,
            workers:((.value.workers // {}) | length)}] else [] end)}'
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
    users=$(sqlite3 -readonly "$db" "select Username||'|'||Password||'|'||ifnull(Salt,'') from Users order by Id" 2>/dev/null \
      | jq -R -s -c 'split("\n") | map(select(length>0))' 2>/dev/null)
    [ -n "$users" ] && users=$(jq -c --arg fp "$(fp "$users")" '{count:length, fp:$fp, names:map(split("|")[0])}' <<<"$users") || users=null
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
      local host="$app.$tailnet" exp days ok=false ip
      ip=$(jq -r --arg h "$host." '[.Peer[]? | select(.DNSName == $h) | .TailscaleIPs[0]] | first // empty' <<<"$ts")
      [ -n "$ip" ] || ip=$(docker exec edge-tailscale-1 nslookup "$host" 100.100.100.100 2>/dev/null | awk '/^Address/ && $2 !~ /100\.100\.100\.100/ {print $2; exit}')
      exp=$(timeout 15 openssl s_client -servername "$host" -connect "${ip:-$host}:443" </dev/null 2>/dev/null \
            | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
      if [ -n "$exp" ]; then ok=true; days=$(( ($(date -d "$exp" +%s) - now) / 86400 )); else days=null; fi
      jq -c -n --arg h "$host" --argjson ok "$ok" --argjson d "$days" '{host:$h, ok:$ok, days_left:$d}'
    done | jq -s '.')
  fi
  j -n --argjson t "$tsj" --argjson c "$certs" '{tailscale:$t, certs:$c}'
}

# ---------- assemble ----------
report=$(j -n --arg host "$(hostname)" --argjson ts "$now" --argjson v "$AGENT_VERSION" \
  --argjson host_j "$(host_json)" --argjson disks "$(disks_json)" --argjson mounts "$(mounts_json)" \
  --argjson backup "$(backup_json)" --argjson docker "$(docker_json)" --argjson media "$(media_json)" \
  --argjson edge "$(edge_json)" --argjson logins "$(logins_json)" \
  '{host:$host, ts:$ts, agent_version:$v, system:$host_j, disks:$disks, mounts:$mounts, backup:$backup,
    docker:$docker, media:$media, logins:$logins, edge:$edge}')

if [ "${1:-}" = "--print" ]; then
  jq . <<<"$report"
else
  curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d "$report" "$REPORT_URL" >/dev/null \
    || logger -t homelab-agent "could not deliver report to $REPORT_URL"
fi
