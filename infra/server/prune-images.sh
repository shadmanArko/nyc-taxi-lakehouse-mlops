#!/usr/bin/env bash
# Weekly image housekeeping on the server (taxi-image-prune.timer).
# For each taxi-* repository keep every image a container uses (running or sleeping:
# scale-to-zero stops containers, it does not remove them) plus the KEEP newest, so the
# previous release stays available for a rollback. Then drop dangling layers and old
# build cache. Never touches images of other projects.
set -euo pipefail

KEEP=${KEEP:-2}
DRY_RUN=${DRY_RUN:-0}   # DRY_RUN=1: only list what would be removed
log() { echo "$(date -u +%FT%TZ) $*"; }

in_use=$(docker ps -a --format '{{.Image}}' | { xargs -r docker image inspect --format '{{.Id}}' 2>/dev/null || true; } | sort -u)

repos=$(docker image ls --format '{{.Repository}}' | grep -E '(^|/)taxi-[a-z]+$' | sort -u || true)
for repo in $repos; do
  i=0
  # newest first, one line per image id
  for short in $(docker image ls "$repo" --format '{{.CreatedAt}}|{{.ID}}' | sort -r | cut -d'|' -f2 | awk '!seen[$0]++'); do
    id=$(docker image inspect --format '{{.Id}}' "$short")
    if (( i < KEEP )) || grep -qx "$id" <<<"$in_use"; then
      log "keeping  $repo $short"
    elif [[ "$DRY_RUN" == 1 ]]; then
      log "would remove $repo $short"
    else
      log "removing $repo $short"
      docker image rm --force "$short" >/dev/null || log "could not remove $short (still referenced)"
    fi
    i=$((i + 1))
  done
done

[[ "$DRY_RUN" == 1 ]] && { log "dry run: nothing removed"; exit 0; }
docker image prune --force >/dev/null                              # dangling layers only
docker builder prune --force --filter "until=168h" >/dev/null      # build cache > 7 days
log "done; disk: $(df -h / | awk 'NR==2 {print $4" free"}')"
