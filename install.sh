#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ $(id -u) != 0 ]]; then
    printf '%s\n' 'Run this installer on the Linux Docker host with sudo.' >&2
    exit 1
fi
command -v python3 >/dev/null
docker compose version
docker info --format '{{.ServerVersion}}' >/dev/null

if [[ ! -d runtime ]]; then
    python3 install.py prepare "$@"
elif [[ $# != 0 ]]; then
    printf '%s\n' 'Runtime already exists. Run without flags to resume or specify a separate directory with install.py.' >&2
    exit 1
fi
exec python3 install.py start
