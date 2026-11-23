#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RUNTIME="$ROOT/runtime"
[[ -f "$RUNTIME/compose.yaml" ]] || { echo "Run prepare/start first" >&2; exit 1; }
compose=(docker compose --project-directory "$RUNTIME" -f "$RUNTIME/compose.yaml")
echo "== Compose validation =="
"${compose[@]}" config --quiet
echo "== Container status =="
"${compose[@]}" ps
echo "== Authorization Python syntax =="
"${compose[@]}" exec -T authorization python -m py_compile /app/api.py /app/registration.py
echo "== Database tables =="
"${compose[@]}" exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 -Atc \
  "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'matrix_lan_%' ORDER BY tablename"
echo "== HTTPS setup endpoint =="
HOST="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["host"])' "$RUNTIME/bootstrap.private.json")"
PORT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["port"])' "$RUNTIME/bootstrap.private.json")"
BIND="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bind"])' "$RUNTIME/bootstrap.private.json")"
curl --fail --silent --show-error --insecure --resolve "$HOST:$PORT:$BIND" \
  "https://$HOST:$PORT/api/setup/status"
printf '\n== Onboarding page ==\n'
curl --fail --silent --show-error --insecure --resolve "$HOST:$PORT:$BIND" \
  -o /dev/null -w 'HTTP %{http_code}\n' "https://$HOST:$PORT/matrix-lan/onboarding.html"
echo "== Finished: no user accounts were created =="
