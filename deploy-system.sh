#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEST=/opt/matrix-lan
[[ "$SOURCE" != "$DEST" ]] || { echo "Already in deployment tree" >&2; exit 1; }
[[ $# -ge 1 ]] || { echo "Usage: sudo bash deploy-system.sh install [prepare options]" >&2; exit 2; }
action="$1"
shift
[[ "$action" == install ]] || { echo "Only fresh install is supported; existing data is never overwritten." >&2; exit 2; }
[[ ! -e "$DEST" && ! -L "$DEST" ]] || { echo "Deployment already exists; refusing overwrite." >&2; exit 1; }
[[ ! -e "$SOURCE/runtime" ]] || { echo "Existing source runtime detected. Back up or explicitly reset it before fresh deployment." >&2; exit 1; }
umask 077
install -d -o root -g root -m 0700 "$DEST"
# Export committed source only; exclude runtime, untracked secrets and Git metadata.
git -C "$SOURCE" archive HEAD | tar --extract --directory="$DEST" --no-same-owner --no-same-permissions
chown -R root:root "$DEST"
chmod -R go-w "$DEST"
cd "$DEST"
bash docker-install.sh prepare "$@"
bash docker-install.sh start
