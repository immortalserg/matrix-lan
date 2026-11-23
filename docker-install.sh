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

IMAGE="matrix-lan-installer:local"
docker build -t "$IMAGE" - < "$ROOT/Dockerfile.installer"

# Socket access effectively grants root privileges on the Docker host.
# Never expose this container or mount the socket into any runtime bridge.
docker run --rm --user 0:0 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$ROOT:$ROOT" \
  -w "$ROOT" \
  --entrypoint python3 \
  "$IMAGE" "$ROOT/install.py" "$@"
