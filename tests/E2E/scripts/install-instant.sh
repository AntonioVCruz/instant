#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
e2e_dir=$(cd -- "$script_dir/.." && pwd)
repo_dir=$(cd -- "$e2e_dir/../.." && pwd)
action=${1:-install}
project=${INSTANT_E2E_PROJECT:-instant-e2e}

fail() { printf '%s\n' "$*" >&2; exit 1; }
for tool in docker python3 curl git flock realpath; do
  command -v "$tool" >/dev/null || fail "Missing dependency: $tool"
done
[[ $project =~ ^instant-e2e(-[a-z0-9-]+)?$ ]] || fail "Project must be instant-e2e or instant-e2e-<suffix>."
[[ -n ${INSTANT_E2E_STATE_DIR:-} ]] || fail "Set INSTANT_E2E_STATE_DIR to a private directory outside the checkout."
state_dir=$(realpath -m -- "$INSTANT_E2E_STATE_DIR")
case "$state_dir/" in "$repo_dir/"*) fail "Runtime state must be outside the checkout.";; esac
mkdir -p -- "$state_dir"
exec 9>"$state_dir/installer.lock"
flock -n 9 || fail "Another installer operation holds the state lock."
docker info >/dev/null 2>&1 || fail "Start a Docker Linux engine before running this script."
docker compose version >/dev/null

if [[ ! -f $state_dir/.env ]]; then
  [[ $action == install ]] || fail "No environment exists yet. Run install first."
  source_sha=$(git -C "$repo_dir" rev-parse HEAD)
  python3 - "$state_dir" "$project" "$source_sha" <<'PY'
import os, pathlib, secrets, sys, uuid
state, project, sha = sys.argv[1:]
values = {
    'INSTANT_E2E_PROJECT': project,
    'APP_SOURCE_SHA': sha,
    'APP_IMAGE_TAG': sha[:12],
    'INSTANCE_ID': str(uuid.uuid4()),
    'POSTGRES_PASSWORD': secrets.token_hex(24),
    'MINIO_ROOT_USER': 'e2e' + secrets.token_hex(8),
    'MINIO_ROOT_PASSWORD': secrets.token_hex(24),
    'INSTANT_E2E_HOST': os.environ.get('INSTANT_E2E_HOST', '127.0.0.1'),
    'DASHBOARD_PORT': os.environ.get('DASHBOARD_PORT', '13000'),
    'BACKEND_PORT': os.environ.get('BACKEND_PORT', '18888'),
    'STORAGE_PORT': os.environ.get('STORAGE_PORT', '19000'),
}
ports = [values[key] for key in ('DASHBOARD_PORT', 'BACKEND_PORT', 'STORAGE_PORT')]
if values['INSTANT_E2E_HOST'] not in {'127.0.0.1', 'localhost'}:
    raise SystemExit('INSTANT_E2E_HOST must be 127.0.0.1 or localhost.')
if len(set(ports)) != 3 or any(not p.isdigit() or not 1024 <= int(p) <= 65535 for p in ports):
    raise SystemExit('Choose three distinct, unprivileged port numbers.')
path = pathlib.Path(state) / '.env'
with path.open('x', encoding='utf-8') as f:
    f.writelines(f'{key}={value}\n' for key, value in values.items())
path.chmod(0o600)
PY
fi

# The file is generated locally, never copied from a suite or another project.
set -a
# shellcheck source=/dev/null
source "$state_dir/.env"
set +a
[[ $project == "$INSTANT_E2E_PROJECT" ]] || fail "State belongs to a different Compose project."
# BuildKit preserves CRLF inside COPY heredocs, including Linux shebangs. Keep
# the checkout intact and normalize only the private Dockerfile inputs.
if [[ $action == install ]]; then
  python3 - "$repo_dir" "$state_dir" <<'PY'
import pathlib, sys
repo, state = map(pathlib.Path, sys.argv[1:])
target = state / 'dockerfiles'
target.mkdir(exist_ok=True)
for source, name in [('server/Dockerfile.self-hosted', 'server.Dockerfile'),
                     ('client/Dockerfile.www', 'dashboard.Dockerfile')]:
    (target / name).write_bytes((repo / source).read_bytes().replace(b'\r\n', b'\n'))
PY
fi
dc() { docker compose --env-file "$state_dir/.env" -p "$project" -f "$e2e_dir/compose.yaml" "$@"; }
dc config --quiet
for suffix in postgres-data minio-data server-config; do
  if docker volume inspect "${project}_${suffix}" >/dev/null 2>&1; then
    instance=$(docker volume inspect "${project}_${suffix}" --format '{{index .Labels "org.instantdb.e2e.instance"}}')
    [[ $instance == "$INSTANCE_ID" ]] || fail "Project volume belongs to different private state. Choose a new project name."
  fi
done

ready() {
  local deadline=$((SECONDS + ${INSTANT_E2E_READY_TIMEOUT:-300}))
  local service container state
  while (( SECONDS < deadline )); do
    if python3 "$script_dir/probe-instant.py" --quiet; then
      python3 "$script_dir/probe-instant.py"
      return
    fi
    for service in server www; do
      container=$(dc ps -a -q "$service")
      if [[ -n $container ]]; then
        state=$(docker inspect "$container" --format '{{.State.Status}}')
        [[ $state != exited && $state != dead ]] || fail "$service exited during startup. Inspect its private logs."
      fi
    done
    sleep 3
  done
  dc ps -a
  fail "Readiness timed out. Use logs to inspect this instance privately."
}

case "$action" in
  install)
    [[ $(git -C "$repo_dir" rev-parse HEAD) == "$APP_SOURCE_SHA" ]] || fail "Checkout revision changed; use a new project/state directory."
    # Git for Windows checks out CRLF; WSL otherwise compares those bytes with
    # LF blobs as if every line changed. Normalize without editing Git config.
    git -c core.autocrlf=true -C "$repo_dir" diff --quiet "$APP_SOURCE_SHA" -- client server || fail "Application source has tracked changes; pin a separate revision first."
    dc pull postgres
    if [[ ${2:-} != --no-build ]]; then
      # Stdin avoids Bake filesystem entitlements for private Dockerfiles that
      # live outside the source context.
      docker build --label "org.opencontainers.image.revision=$APP_SOURCE_SHA" \
        --label org.opencontainers.image.source=https://github.com/instantdb/instant \
        -t "$project/server:$APP_IMAGE_TAG" -f - "$repo_dir/server" < "$state_dir/dockerfiles/server.Dockerfile"
      docker build --label "org.opencontainers.image.revision=$APP_SOURCE_SHA" \
        --label org.opencontainers.image.source=https://github.com/instantdb/instant \
        --build-arg NODE_VERSION=22.22.2-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e \
        -t "$project/www:$APP_IMAGE_TAG" -f - "$repo_dir/client" < "$state_dir/dockerfiles/dashboard.Dockerfile"
      docker build -t "$project/storage:9e49d5e7a648" -f "$e2e_dir/docker/Minio.Dockerfile" "$e2e_dir/docker"
    fi
    for service in server www; do
      revision=$(docker image inspect "$project/$service:$APP_IMAGE_TAG" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
      [[ $revision == "$APP_SOURCE_SHA" ]] || fail "$service image does not match the pinned source."
      docker run --rm --network none --entrypoint /bin/sh "$project/$service:$APP_IMAGE_TAG" \
        -c '! grep -q "$(printf "\r")" /app/start.sh' || fail "$service startup contains CRLF. Run install without --no-build."
    done
    revision=$(docker image inspect "$project/storage:9e49d5e7a648" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
    [[ $revision == 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a ]] || fail "Storage image does not match its pinned source."
    dc up -d --no-build --pull never
    ready
    ;;
  ready) ready;;
  status) dc ps -a;;
  logs) dc logs --no-color --tail 100 "${2:-server}";;
  restart)
    dc restart server www
    ready
    ;;
  reset)
    [[ ${2:-} == --confirm-reset && ${3:-} == "$project" ]] || fail "Reset requires: reset --confirm-reset $project"
    # Only the two data volumes owned by this exact project are reset. Config
    # keys, private credentials, source-built images and other projects survive.
    for suffix in postgres-data minio-data; do
      owner=$(docker volume inspect "${project}_${suffix}" --format '{{index .Labels "com.docker.compose.project"}}')
      [[ $owner == "$project" ]] || fail "Refusing to reset an unowned volume."
    done
    dc down
    docker volume rm "${project}_postgres-data" "${project}_minio-data" >/dev/null
    dc up -d --no-build --pull never
    ready
    ;;
  stop) dc stop;;
  *) fail "Usage: $0 {install [--no-build]|ready|status|logs [service]|restart|reset --confirm-reset $project|stop}";;
esac
