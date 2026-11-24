#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ "$ROOT" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo "Unsupported project path" >&2; exit 1; }
[[ -f "$ROOT/runtime/compose.yaml" ]] || { echo "Prepare runtime first" >&2; exit 1; }
cat > /etc/systemd/system/matrix-lan-controller.service <<EOF
[Unit]
Description=Matrix-LAN bridge reconciliation
After=docker.service
Requires=docker.service
ConditionPathExists=$ROOT/runtime/compose.yaml
[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $ROOT/services/controller/reconcile.py
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
