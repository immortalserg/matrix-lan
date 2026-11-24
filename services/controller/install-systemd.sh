#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ "$ROOT" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo "Unsupported project path" >&2; exit 1; }
[[ -f "$ROOT/runtime/compose.yaml" ]] || { echo "Prepare runtime first" >&2; exit 1; }
# A privileged recurring service must not execute files from a user-writable
# checkout. Require an explicitly trusted root-owned installation tree.
python3 - "$ROOT" <<'PY'
import os, stat, sys
from pathlib import Path
root = Path(sys.argv[1])
paths = [root, *root.parents, root / "services", root / "services/controller",
         root / "services/controller/reconcile.py", root / "templates",
         root / "templates/bridge-catalog.json", root / "runtime",
         root / "runtime/compose.yaml", root / "runtime/bootstrap.private.json"]
for path in paths:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        sys.exit("Controller requires trusted root-owned paths: " + str(path))
PY
# Install a root-owned snapshot of executable code and the static allowlist.
# Runtime remains in the trusted installation tree, not in the source snapshot.
DEST=/usr/local/lib/matrix-lan-controller
install -d -o root -g root -m 0755 "$DEST" "$DEST/services" "$DEST/services/controller" "$DEST/templates"
install -o root -g root -m 0644 "$ROOT/services/controller/reconcile.py" "$DEST/services/controller/reconcile.py"
install -o root -g root -m 0644 "$ROOT/templates/bridge-catalog.json" "$DEST/templates/bridge-catalog.json"
cat > /etc/systemd/system/matrix-lan-controller.service <<EOF
[Unit]
Description=Matrix-LAN bridge reconciliation
After=docker.service
Requires=docker.service
ConditionPathExists=$ROOT/runtime/compose.yaml
[Service]
Type=oneshot
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 -I $DEST/services/controller/reconcile.py --runtime $ROOT/runtime
UMask=0077
NoNewPrivileges=true
TimeoutStartSec=300
EOF
cat > /etc/systemd/system/matrix-lan-controller.timer <<'EOF'
[Unit]
Description=Apply Matrix-LAN bridge settings
[Timer]
OnBootSec=30s
OnUnitInactiveSec=30s
Unit=matrix-lan-controller.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now matrix-lan-controller.timer
