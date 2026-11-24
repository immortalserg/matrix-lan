#!/usr/bin/env bash
set -euo pipefail

# Run from any directory; bind the project at the same absolute path on host and
# inside the installer so Compose bind mounts resolve to the host's real files.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
command -v docker >/dev/null || { echo 'Docker CLI is required.' >&2; exit 1; }
docker info >/dev/null || { echo 'Docker daemon is not accessible.' >&2; exit 1; }
docker compose version >/dev/null || { echo 'Docker Compose v2 is required.' >&2; exit 1; }

if [[ ! -S /var/run/docker.sock ]]; then
  echo 'Expected /var/run/docker.sock. Set up a local Docker Engine.' >&2
  exit 1
fi

for ((i=1; i<=$#; i++)); do
  if [[ "${!i}" == "--runtime" ]]; then
    next=$((i+1))
    if (( next > $# )); then echo '--runtime requires a path' >&2; exit 2; fi
    runtime="${!next}"
    [[ "$runtime" == /* ]] || runtime="$ROOT/$runtime"
    case "$runtime/" in "$ROOT/"*) ;; *) echo '--runtime must be inside the project directory.' >&2; exit 2 ;; esac
  fi
done

# Destructive test-only lifecycle commands. Keep Git source intact: deleting this
# script while executing it makes reinstall unreliable. Only the Matrix-LAN
# Compose project and its runtime directory are removed.
if [[ "${1:-}" == "reset" || "${1:-}" == "reinstall" ]]; then
  action="$1"
  shift
  [[ $EUID == 0 ]] || { echo "Run reset or reinstall with sudo." >&2; exit 1; }
  [[ "${1:-}" == "--yes" ]] || { echo "Usage: bash docker-install.sh $action --yes [prepare options]" >&2; exit 2; }
  shift
  runtime="$ROOT/runtime"
  [[ "$runtime" != "/" && "$runtime" == "$ROOT/runtime" ]] || exit 2
  if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
    systemctl disable --now matrix-lan-controller.timer 2>/dev/null || true
    systemctl stop matrix-lan-controller.service 2>/dev/null || true
  fi
  if [[ -f "$runtime/compose.yaml" ]]; then
    docker compose --project-directory "$runtime" -f "$runtime/compose.yaml" down --volumes --remove-orphans
  fi
  docker rm -f matrix-lan-admin >/dev/null 2>&1 || true
  if [[ -e "$runtime" ]]; then
    [[ ! -L "$runtime" ]] || { echo "Refusing to remove symlink runtime." >&2; exit 2; }
    rm -rf -- "$runtime"
  fi
  echo "Matrix-LAN runtime and Compose volumes removed; Git working tree retained."
  if [[ "$action" == "reset" ]]; then
    [[ $# -eq 0 ]] || { echo "reset accepts no additional options." >&2; exit 2; }
    exit 0
  fi
  # Re-enter the regular installer flow with explicit fresh-install options.
  bash "$ROOT/docker-install.sh" prepare "$@"
  bash "$ROOT/docker-install.sh" start
  exit 0
fi

IMAGE="matrix-lan-installer:local"
docker build -t "$IMAGE" - < "$ROOT/Dockerfile.installer"

# Administrative UI is deliberately restricted to the host loopback interface.
if [[ "${1:-}" == "admin-start" || "${1:-}" == "admin-password" ]]; then
  action="$1"
  if [[ "$action" == "admin-start" ]]; then
    docker run --rm --user 0:0 \
      -v "$ROOT:$ROOT" -w "$ROOT" \
      --entrypoint python3 "$IMAGE" "$ROOT/admin.py" admin-init
    docker rm -f matrix-lan-admin >/dev/null 2>&1 || true
    docker run -d --name matrix-lan-admin --restart unless-stopped \
      --user 0:0 -p 127.0.0.1:18082:18082 \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$ROOT:$ROOT" -w "$ROOT" \
      --entrypoint python3 "$IMAGE" "$ROOT/admin.py"
    echo "Admin UI: http://localhost:18082/ (login: admin)"
    echo "Get password: bash docker-install.sh admin-password"
  else
    docker run --rm --user 0:0 -v "$ROOT:$ROOT" -w "$ROOT" \
      --entrypoint python3 "$IMAGE" "$ROOT/admin.py" admin-password
  fi
  exit 0
fi

# Socket access effectively grants root privileges on the Docker host.
# Never expose this container or mount the socket into any runtime bridge.
docker run --rm --user 0:0 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$ROOT:$ROOT" \
  -w "$ROOT" \
  --entrypoint python3 \
  "$IMAGE" "$ROOT/install.py" "$@"


# Host-only controller: never install a root service from a writable checkout.
if [[ "${1:-}" == "start" && $# -eq 1 && $EUID == 0 && -d /run/systemd/system ]]; then
  bash "$ROOT/services/controller/install-systemd.sh"
fi
