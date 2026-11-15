#!/usr/bin/env bash
# Run on the ChatMoney production host. Deploy reviewed application code to its existing data volume.
set -Eeuo pipefail
if [[ $# -ne 2 || ! $1 =~ ^[0-9a-f]{40}$ || ! $2 =~ ^[0-9a-f]{64}$ ]]; then
  echo 'Usage: sudo bash publish-app.sh <full-team-commit> <archive-sha256>' >&2
  exit 2
fi
if (( EUID != 0 )); then
  echo 'Run this script with sudo on the team server.' >&2
  exit 1
fi
umask 077
rev=$1
sum=$2
command -v flock >/dev/null
cd /opt/chatmoney
export COMPOSE_PROJECT_NAME=chatmoney-prod
unset COMPOSE_FILE
dc() { docker compose -p chatmoney-prod -f /opt/chatmoney/compose.yaml "$@"; }
install -d -m 700 releases backups
exec 9>/opt/chatmoney/releases/.publish.lock
flock -n 9 || { echo 'Another release is in progress.'; exit 1; }
run=$(mktemp -d "/opt/chatmoney/releases/$rev.XXXXXX")
home_dir=$(getent passwd cfcross | cut -d: -f6)
test -n "$home_dir"
install -m 600 "$home_dir/chatmoney-$rev.tar.gz" "$run/source.tar.gz"
printf '%s  %s\n' "$sum" "$run/source.tar.gz" | sha256sum -c -
mkdir "$run/source"
tar --extract --gzip --file="$run/source.tar.gz" --directory="$run/source" --no-same-owner
new=chatmoney-engine:$rev
old=$(docker inspect --format '{{.Config.Image}}' "$(dc ps -q api)")
app_services=(api worker)
if dc config --services | grep -Fxq practice; then
  test -f "$run/source/app/practice/runner.py" || { echo 'Missing practice runtime in proposed release.' >&2; exit 1; }
  app_services+=(practice)
elif [[ -f "$run/source/app/practice/runner.py" ]]; then
  echo 'Provision the practice service in the reviewed production Compose before upgrading.' >&2; exit 1
fi
if dc config --services | grep -Fxq research; then
  test -f "$run/source/app/research/runner.py" || { echo 'Missing research worker.' >&2; exit 1; }
  app_services+=(research)
elif [[ -f "$run/source/app/research/runner.py" ]]; then
  echo 'Provision the reviewed research service before upgrading.' >&2; exit 1
fi
for svc in "${app_services[@]}"; do
  test "$old" = "$(docker inspect --format '{{.Config.Image}}' "$(dc ps -q "$svc")")"
done
if [[ "$old" == "$new" ]]; then
  test "$(dc config --images | grep -Fxc "$new")" -ge 1
  echo "All enabled application services already use $new."
  exit 0
fi
dbid=$(dc ps -q db)
test -n "$dbid"
volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' "$dbid")
test "$volume" = chatmoney-prod_data
cp -p compose.yaml "$run/compose.before.yaml"
df -h /opt/chatmoney
# Build before stopping any live application service.
docker build --label "org.opencontainers.image.revision=$rev" -t "$new" "$run/source"
test "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$new")" = "$rev"

python3 - "$old" "$new" "$run/compose.next.yaml" <<'PY'
from pathlib import Path
import sys
old, new, dest = sys.argv[1:]
lines = Path("compose.yaml").read_text().splitlines(keepends=True)
hits = [i for i, line in enumerate(lines) if line.strip() == f"image: {old}"]
if len(hits) != 1:
    raise SystemExit("Expected exactly one reference to the running image in the production Compose file.")
i = hits[0]
lines[i] = lines[i].replace(old, new)
Path(dest).write_text("".join(lines))
PY
next() { docker compose --project-directory /opt/chatmoney -p chatmoney-prod -f "$run/compose.next.yaml" "$@"; }
next config --quiet
test "$(next config --images | grep -Fxc "$new")" -ge 1

install_compose() {
  local tmp
  tmp=$(mktemp /opt/chatmoney/.compose.publish.XXXXXX)
  install -m 640 "$1" "$tmp" && mv -f -- "$tmp" /opt/chatmoney/compose.yaml
}
rollback_app() {
  trap - EXIT HUP INT TERM
  set +e
  echo "Release interrupted; restoring $old" >&2
  install_compose "$run/compose.before.yaml" &&
    dc up -d --no-deps --no-build "${app_services[@]}" || echo 'Could not restart the previous application; check service logs.' >&2
  exit 1
}
trap 'if (( $? != 0 )); then rollback_app; fi' EXIT
trap 'exit 1' HUP INT TERM
dc stop -t 150 "${app_services[@]}"
dump="/opt/chatmoney/backups/$(date -u +%Y%m%dT%H%M%SZ)-$rev.dump"
test ! -e "$dump"
dc exec -T db pg_dump -U cm_owner -d chatmoney -Fc > "$dump.partial"
dc exec -T db pg_restore --list < "$dump.partial" > "$run/backup.contents"
grep -q 'TABLE DATA public web_accounts' "$run/backup.contents"
mv -- "$dump.partial" "$dump"
counts() {
  dc exec -T db psql -XAt -v ON_ERROR_STOP=1 -U cm_owner -d chatmoney -c 'SELECT (SELECT count(*) FROM web_accounts), (SELECT count(*) FROM agents), (SELECT count(*) FROM model_connections), (SELECT count(*) FROM documents)'
}
counts > "$run/counts.before"
(
  set -a
  . ./config/migrate.env
  set +a
  next run --rm -T --no-deps -e MIGRATION_DATABASE_URL api python -m scripts.bootstrap
)
counts > "$run/counts.after"
cmp "$run/counts.before" "$run/counts.after"
install_compose "$run/compose.next.yaml"
dc up -d --no-build --no-deps --wait --wait-timeout 90 "${app_services[@]}"
test "$(dc ps -q db)" = "$dbid"
for svc in "${app_services[@]}"; do
  test "$(docker inspect --format '{{.Config.Image}} {{.State.Status}}' "$(dc ps -q "$svc")")" = "$new running"
done
curl -fsS --max-time 12 --resolve agent.heiling.tech:443:127.0.0.1 -o /dev/null https://agent.heiling.tech/console
code=$(curl -sS --max-time 12 --resolve agent.heiling.tech:443:127.0.0.1 -o /dev/null -w '%{http_code}' https://agent.heiling.tech/console/data/workspace)
test "$code" = 401
trap - EXIT HUP INT TERM
printf 'Published %s\nBackup: %s\nPrevious config: %s/compose.before.yaml\n' "$rev" "$dump" "$run"
dc ps
dc logs --tail 15 worker
