#!/bin/bash
# The "ops door": the only thing the n8n sysadmin workflow can run on this VM. It arrives as
# the ops user over SSH (forced command, see homelab-ops-door) and is run through sudo. Each
# verb is fixed, logged to the journal (tag homelab-ops) and answers one JSON line.
#
# Verbs (container = compose service name in /etc/homelab-ops.allow, never arbitrary):
#   status                          docker ps summary
#   logs <service> [lines<=200]     last log lines of a container
#   restart <service>               compose up -d (if missing) or restart (if running)
#   stop <service>                  stop a crash-looping container
#   arr-retry <sonarr|radarr> <queue id>      remove from queue (file kept) and search again
#   arr-blocklist <sonarr|radarr> <queue id>  blocklist that release and search again
#   prowlarr-test <indexer id>      test an indexer, which re-enables a backed-off one
#   docker-prune                    dangling images and build cache only
#   remount-nas                     re-trigger the NAS automount, then recreate the services that bind it
#   plex-remote-on                  switch Plex Remote Access back on, or off and on to re-publish
#   update <service>                pull the service's image tag and recreate it if the image changed;
#                                   only services in /etc/homelab-autoupdate.conf (hosts.yml auto_update)
#
# Never: reboot, poweroff, anything under /mnt/nfs, pulls beyond the auto-update list, settings
# changes (the one exception: Plex Remote Access back on, which Goobs keeps on; D05 in the wiki).
set -uo pipefail
ALLOW=/etc/homelab-ops.allow            # "stack/service" per line, written by Ansible
CONF=/etc/homelab-agent.conf
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"
NAS_IP=${NAS_IP:-192.168.1.209}
APPDATA=/opt/appdata

verb=${1:-}; shift || true
log() { logger -t homelab-ops "$verb ${*:-}"; }
out() { jq -c -n --arg verb "$verb" --arg result "$1" --arg detail "${2:-}" '{verb:$verb, result:$result, detail:$detail}'; }
die() { log "refused: $1"; out refused "$1"; exit 2; }

# Resolve a service name to its stack and container name through the allow-list.
resolve() {
  local svc=$1 line
  [[ "$svc" =~ ^[a-z0-9-]+$ ]] || die "bad service name"
  line=$(grep -x -m1 "[a-z0-9-]*/$svc" "$ALLOW" 2>/dev/null) || die "service $svc is not on the allow-list"
  stack=${line%%/*}; container="$stack-$svc-1"
}
arr_key() { sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$APPDATA/$1/config.xml" 2>/dev/null | head -1; }
arr_base() { case "$1" in sonarr) echo "http://127.0.0.1:8989/api/v3";; radarr) echo "http://127.0.0.1:7878/api/v3";; *) die "app must be sonarr or radarr";; esac; }
arr_search() { # app ref-id
  local app=$1 ref=$2 cmd ids
  case "$app" in sonarr) cmd=SeriesSearch; ids="\"seriesId\":$ref";; radarr) cmd=MoviesSearch; ids="\"movieIds\":[$ref]";; esac
  curl -fsS -m 20 -H "X-Api-Key: $(arr_key "$app")" -H 'Content-Type: application/json' \
    -X POST "$(arr_base "$app")/command" -d "{\"name\":\"$cmd\",$ids}" >/dev/null
}

case "$verb" in
  status)
    log
    docker ps -a --format '{{json .}}' | jq -s -c 'map({name:.Names, status:.Status, image:.Image})'
    ;;

  logs)
    resolve "${1:-}"; n=${2:-100}; [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -le 200 ] || n=100
    log "$container $n"
    docker logs --tail "$n" "$container" 2>&1 | jq -R -s -c --arg c "$container" '{container:$c, log:.}'
    ;;

  restart)
    resolve "${1:-}"
    before=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo absent)
    log "$container (was $before)"
    if [ "${before%%/*}" = running ]; then
      (cd "/opt/stacks/$stack" && docker compose restart -t 30 "${1}") >/dev/null 2>&1 || { out failed "compose restart $1 failed"; exit 1; }
    else
      (cd "/opt/stacks/$stack" && docker compose up -d --no-deps "${1}") >/dev/null 2>&1 || { out failed "compose up $1 failed"; exit 1; }
    fi
    sleep 5
    after=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo absent)
    out ok "$container: $before -> $after"
    ;;

  stop)
    resolve "${1:-}"
    log "$container"
    docker stop -t 30 "$container" >/dev/null 2>&1 && out ok "$container stopped; undo: ssh goobs@$(hostname -I | cut -d' ' -f1) 'cd /opt/stacks/$stack && sudo docker compose up -d $1'" || out failed "stop failed"
    ;;

  arr-retry|arr-blocklist)
    app=${1:-}; id=${2:-}
    [[ "$id" =~ ^[0-9]+$ ]] || die "queue id must be a number"
    base=$(arr_base "$app"); key=$(arr_key "$app"); [ -n "$key" ] || die "no API key for $app"
    item=$(curl -fsS -m 20 -H "X-Api-Key: $key" "$base/queue/details?pageSize=500" 2>/dev/null | jq -c --argjson id "$id" 'map(select(.id==$id)) | .[0] // empty')
    [ -n "$item" ] || item=$(curl -fsS -m 20 -H "X-Api-Key: $key" "$base/queue?pageSize=500" 2>/dev/null | jq -c --argjson id "$id" '(.records // []) | map(select(.id==$id)) | .[0] // empty')
    [ -n "$item" ] || die "queue item $id not found in $app"
    title=$(jq -r '.title // "?"' <<<"$item"); ref=$(jq -r '.movieId // .seriesId // empty' <<<"$item")
    if [ "$verb" = arr-retry ]; then q="removeFromClient=false&blocklist=false"; else q="removeFromClient=true&blocklist=true"; fi
    log "$app $id '$title'"
    curl -fsS -m 20 -H "X-Api-Key: $key" -X DELETE "$base/queue/$id?$q" >/dev/null 2>&1 || { out failed "queue delete $id failed"; exit 1; }
    [ -n "$ref" ] && arr_search "$app" "$ref"
    out ok "$app queue $id '$title' removed ($q); search triggered for ${ref:-nothing}"
    ;;

  prowlarr-test)
    id=${1:-}; [[ "$id" =~ ^[0-9]+$ ]] || die "indexer id must be a number"
    key=$(arr_key prowlarr); [ -n "$key" ] || die "no API key for prowlarr"
    # A failed read is "failed", not "refused": n8n holds a refused fix for a day, a failed one is retried.
    body=$(curl -fsS -m 20 -H "X-Api-Key: $key" "http://127.0.0.1:9696/api/v1/indexer/$id" 2>/dev/null) \
      || { log "could not read indexer $id"; out failed "could not read indexer $id from Prowlarr"; exit 1; }
    name=$(jq -r '.name' <<<"$body"); log "$id $name"
    if curl -fsS -m 60 -H "X-Api-Key: $key" -H 'Content-Type: application/json' -X POST "http://127.0.0.1:9696/api/v1/indexer/test" -d "$body" >/dev/null 2>&1; then
      out ok "indexer $name tested fine (backoff cleared)"
    else out failed "indexer $name still failing"; fi
    ;;

  docker-prune)
    log
    a=$(docker image prune -f 2>/dev/null | tail -1); b=$(docker builder prune -f 2>/dev/null | tail -1)
    out ok "images: ${a:-nothing}; build cache: ${b:-nothing}"
    ;;

  remount-nas)
    [ "${NFS_EXPECTED:-false}" = true ] || die "this VM has no NFS library mount"
    # The NAS going quiet between n8n's check and this one is "failed" (retried in an hour), not "refused".
    timeout 5 bash -c "</dev/tcp/$NAS_IP/2049" 2>/dev/null \
      || { log "NAS does not answer on 2049"; out failed "NAS does not answer on 2049; nothing to remount"; exit 1; }
    log
    umount -l /mnt/nfs 2>/dev/null; sleep 2
    # A hard mount that hangs again would hang this script too: give the automount 45 s (its own
    # mount timeout is 30 s), and KILL, since a process stuck on NFS ignores TERM.
    listing=$(timeout -s KILL 45 ls -A /mnt/nfs 2>/dev/null) || true
    if findmnt -rn -t nfs,nfs4 --mountpoint /mnt/nfs >/dev/null && [ -n "$listing" ]; then
      # Bind mounts inside the containers still point at the old mount: recreate only the services
      # that bind /mnt/nfs (NFS_SERVICES, written by Ansible from the compose files and the
      # allow-list, so never n8n, a database or anything on ops_never), one stack at a time, without
      # their dependencies. Only the ones that are up: a stopped one was stopped on purpose (a
      # crash-loop stop or a chat stop the sysadmin holds), and compose up would start it again.
      done_list="" left=""
      for s in ${STACKS:-}; do
        svcs=()
        for pair in ${NFS_SERVICES:-}; do
          [ "${pair%%/*}" = "$s" ] && grep -qx "$pair" "$ALLOW" 2>/dev/null || continue
          st=$(docker inspect -f '{{.State.Status}}' "$s-${pair#*/}-1" 2>/dev/null) || st=absent
          case "$st" in running|restarting) svcs+=("${pair#*/}");; *) left="$left ${pair#*/} ($st)";; esac
        done
        [ "${#svcs[@]}" -gt 0 ] || continue
        (cd "/opt/stacks/$s" && docker compose up -d --no-deps --force-recreate "${svcs[@]}") >/dev/null 2>&1 \
          || { out failed "/mnt/nfs remounted, but recreating ${svcs[*]} failed"; exit 1; }
        done_list="$done_list ${svcs[*]}"
      done
      out ok "/mnt/nfs remounted and non-empty; recreated:${done_list:- nothing}${left:+; left as they were:$left}"
    else out failed "/mnt/nfs still not mounted or empty"; fi
    ;;

  plex-remote-on)
    # Off: switch it on. Already on but plex.tv dropped the server: off and on again, which makes
    # Plex publish itself to plex.tv again. The token stays on this VM (Plex on localhost only).
    prefs="$APPDATA/plex/Library/Application Support/Plex Media Server/Preferences.xml"
    [ -r "$prefs" ] || die "no Plex on this VM"
    tok=$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$prefs" | head -1)
    [ -n "$tok" ] || die "Plex is not signed in to plex.tv (no token); sign in from Plex Web"
    plex() { curl -fsS -m 20 -H "X-Plex-Token: $tok" -H 'Accept: application/json' "$@"; }
    was=$(plex 'http://127.0.0.1:32400/:/prefs' 2>/dev/null \
      | jq -r '.MediaContainer.Setting[]? | select(.id == "PublishServerOnPlexOnlineKey") | .value')
    [ "$was" = true ] || [ "$was" = false ] || { log "Plex did not answer"; out failed "Plex does not answer on 32400"; exit 1; }
    log "publish was $was"
    if [ "$was" = true ]; then plex -X PUT 'http://127.0.0.1:32400/:/prefs?PublishServerOnPlexOnlineKey=0' >/dev/null 2>&1; sleep 3; fi
    plex -X PUT 'http://127.0.0.1:32400/:/prefs?PublishServerOnPlexOnlineKey=1' >/dev/null 2>&1 \
      || { out failed "Plex refused the Remote Access setting"; exit 1; }
    # Plex publishes and plex.tv tests the port within seconds.
    sleep 20
    st=$(plex http://127.0.0.1:32400/myplex/account 2>/dev/null \
      | jq -r '.MyPlex | (.mappingState // "?") + (if (.mappingError // "") != "" then " (" + .mappingError + ")" else "" end)')
    out ok "Plex Remote Access on (was $([ "$was" = true ] && echo "on, re-published" || echo off)); mapping ${st:-unknown}"
    ;;

  update)
    # Goobs, 2026-10-26: internet-facing media apps update themselves (security fixes); the rest
    # stays by hand. The config bind mounts under /opt/appdata are kept, so settings survive.
    resolve "${1:-}"
    grep -qE "^$stack( .*)? $1( |$)" /etc/homelab-autoupdate.conf 2>/dev/null || die "$1 is not on the auto-update list (hosts.yml auto_update)"
    f="/opt/stacks/$stack/compose.yaml"
    before=$(docker inspect -f '{{.Image}}' "$container" 2>/dev/null | cut -c8-19)
    log "$container (image ${before:-none})"
    # The door cuts every verb off at 150 s: 100 s for the pull (layers already fetched are kept,
    # so a retry picks up where this one stopped), the rest for the recreate.
    timeout 100 docker compose -f "$f" pull -q "$1" >/dev/null 2>&1 || { out failed "pulling the $1 image failed or took over 100 s"; exit 1; }
    after_pull=$(docker image inspect -f '{{.Id}}' "$(docker inspect -f '{{.Config.Image}}' "$container" 2>/dev/null)" 2>/dev/null | cut -c8-19)
    if [ -n "$before" ] && [ "$after_pull" = "$before" ]; then out ok "$1 already current (image $before)"; exit 0; fi
    # --no-deps: never recreate a database or gluetun along with the app.
    docker compose -f "$f" up -d --no-deps "$1" >/dev/null 2>&1 || { out failed "compose up $1 failed after the pull"; exit 1; }
    sleep 5
    after=$(docker inspect -f '{{.Image}}' "$container" 2>/dev/null | cut -c8-19)
    st=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo absent)
    docker image prune -f >/dev/null 2>&1 # the old image, now dangling; tagged images stay
    out ok "$1 updated: image ${before:-none} -> ${after:-?}, now $st"
    ;;

  ""|help) die "no verb given";;
  *) die "unknown verb $verb";;
esac
