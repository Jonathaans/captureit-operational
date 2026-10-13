#!/usr/bin/env bash
# Deploy origin/main from a clean worktree; retain VPS commits and the data volume.
set -Eeuo pipefail
umask 077
ops_source=${OPS_SOURCE_ROOT:-$PWD}
ops_source=$(cd -- "$ops_source" && pwd)
ops_project=${OPS_COMPOSE_PROJECT:-captureit-operational}
ops_service=captureit-ops
ops_releases=${OPS_RELEASE_ROOT:-$(dirname -- "$ops_source")/captureit-operational-releases}
ops_phase=prepare
ops_fail() {
    local code=$?
    printf '\nDeploy stopped during %s (exit %s). Original checkout and data volume were preserved.\n' "$ops_phase" "$code" >&2
    if [[ "$ops_phase" == start || "$ops_phase" == health ]]; then
        docker compose -p "$ops_project" -f "$ops_release/docker-compose.yml" logs --tail=40 "$ops_service" >&2 || true
        printf 'Inspect the error and the backup printed above before retrying.\n' >&2
    fi
    exit "$code"
}
trap ops_fail ERR
command -v git >/dev/null
command -v docker >/dev/null
command -v python3 >/dev/null
test -f "$ops_source/.env"
test -f "$ops_source/deploy/backup-vps.sh"
test "$(id -u)" -eq 0 || { echo 'Run as root, as required by backup-vps.sh.' >&2; exit 1; }
ops_phase=fetch
git -C "$ops_source" fetch origin main
ops_revision=$(git -C "$ops_source" rev-parse --verify origin/main^{commit})
git -C "$ops_source" cat-file -e "$ops_revision:deploy/update-vps.sh"
ops_phase='verify existing volume'
ops_container=$(docker ps --all --quiet --filter "label=com.docker.compose.project=$ops_project" --filter "label=com.docker.compose.service=$ops_service")
test -n "$ops_container"
test "$(printf '%s\n' "$ops_container" | wc -l)" -eq 1
test "$(docker inspect --format '{{.State.Running}}' "$ops_container")" = true
ops_mounts=$(docker inspect --format '{{json .Mounts}}' "$ops_container")
python3 - "$ops_mounts" "${ops_project}_ops-data" <<'PY'
import json,sys
mounts=[m for m in json.loads(sys.argv[1]) if m.get('Destination')=='/data']
if len(mounts)!=1 or mounts[0].get('Type')!='volume' or mounts[0].get('Name')!=sys.argv[2]:
    raise SystemExit('Existing /data does not use the expected Compose volume. Reconcile the Compose configuration first; no new container was started.')
PY
ops_phase=checkout
mkdir -p -- "$ops_releases"
ops_release="$ops_releases/$(date -u +%Y%m%dT%H%M%SZ)-${ops_revision:0:12}"
git -C "$ops_source" worktree add --detach "$ops_release" "$ops_revision"
cp -- "$ops_source/.env" "$ops_release/.env"
chmod 600 "$ops_release/.env"
export OPS_BUILD_REVISION="$ops_revision"
ops_compose=(docker compose -p "$ops_project" -f "$ops_release/docker-compose.yml")
"${ops_compose[@]}" config --quiet
ops_phase=build
"${ops_compose[@]}" build "$ops_service"
ops_phase=backup
OPS_COMPOSE_PROJECT="$ops_project" bash "$ops_source/deploy/backup-vps.sh"
ops_phase=start
"${ops_compose[@]}" up -d --no-deps "$ops_service"
ops_phase=health
ops_ready=0
for ops_try in {1..15}; do
    if "${ops_compose[@]}" exec -T "$ops_service" python3 -c 'import json,sys,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/health",timeout=2)); sys.exit(0 if d.get("revision")==sys.argv[1] and d.get("ok") else 1)' "$ops_revision"; then
        ops_ready=1
        break
    fi
    sleep 2
done
test "$ops_ready" -eq 1
printf '\nDeploy OK\nRevision: %s\nRelease: %s\nProject: %s\n' "$ops_revision" "$ops_release" "$ops_project"
printf 'Use the release directory for Compose logs. Keep .env updates in the original source directory for future releases.\n'
