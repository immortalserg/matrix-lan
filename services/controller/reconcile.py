#!/usr/bin/env python3
"""Run on the trusted Docker host, never in the public HTTP service."""
import argparse
import json
import os
import re
import tempfile
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def reconcile(runtime):
    runtime = runtime.resolve()
    catalog = json.loads((ROOT / "templates/bridge-catalog.json").read_text())
    settings = json.loads((runtime / "bootstrap.private.json").read_text())
    compose = ["docker", "compose", "--project-directory", str(runtime),
               "-f", str(runtime / "compose.yaml")]
    result = subprocess.run(compose + ["exec", "-T", "db", "psql", "-U",
        "postgres", "-d", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
        "-c", "SELECT COALESCE(json_object_agg(bridge, desired_enabled), '{}') FROM matrix_lan_bridge_settings"],
        check=True, capture_output=True, text=True)
    preferences = json.loads(result.stdout)
    failed = False
    credentials = subprocess.run(compose + ["exec", "-T", "db", "psql", "-U",
        "postgres", "-d", "postgres", "-At", "-v", "ON_ERROR_STOP=1",
        "-c", "SELECT json_build_object('api_id',api_id,'api_hash',api_hash) FROM matrix_lan_telegram_credentials WHERE singleton"],
        check=True, capture_output=True, text=True).stdout.strip()
    if credentials and "mautrix-telegram" in settings["services"]:
        data = json.loads(credentials)
        if type(data["api_id"]) is not int or not 0 < data["api_id"] <= 2147483647 or not re.fullmatch(r"[a-fA-F0-9]{32}", data["api_hash"]):
            raise ValueError("Invalid stored Telegram credentials")
        path = runtime / "state/telegram/config.yaml"
        config = json.loads(path.read_text())
        network = config["network"]
        if any(network.get(key) != data[key] for key in ("api_id", "api_hash")):
            network.update(data)
            stat = path.stat()
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
                temp = Path(out.name)
                json.dump(config, out, ensure_ascii=False, indent=2)
            try:
                os.chown(temp, stat.st_uid, stat.st_gid)
                os.chmod(temp, 0o600)
                # Stop before replacing configuration; never delete session data.
                subprocess.run(compose + ["stop", "mautrix-telegram"], check=True)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        # Retry startup on later passes even if the config is already written.
        preferences.setdefault("telegram", True)
    for bridge in catalog["bridges"]:
        service = bridge.get("service")
        if not bridge.get("available", True) or service not in settings["services"]:
            continue
        if bridge["id"] not in preferences:
            continue
        enabled = preferences[bridge["id"]]
        if type(enabled) is not bool:
            continue
        # Only static, bundled service names may reach Docker Compose.
        args = ["up", "-d", "--no-deps", "--no-build", service] if enabled else ["stop", service]
        proc = subprocess.run(compose + args, check=False)
        failed |= proc.returncode != 0
    # Report only lifecycle metadata, never container environment or logs.
    result = subprocess.run(compose + ["ps", "-a", "--format", "json"],
                            check=True, capture_output=True, text=True)
    raw = result.stdout.strip()
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line.strip()]
    states = {row.get("Service"): row for row in rows}
    for bridge in catalog["bridges"]:
        service = bridge.get("service")
        if not service or service not in settings["services"]:
            continue
        row = states.get(service, {})
        state = row.get("State", "missing")
        if state not in ("running", "exited", "created", "restarting", "paused", "dead", "missing", "removing"):
            state = "unknown"
        health = row.get("Health", "")
        if health not in ("healthy", "unhealthy", "starting"):
            health = "unknown"
        # Bridge IDs originate exclusively in the static catalog.
        bridge_id = bridge["id"]
        if not bridge_id.isascii() or not bridge_id.isalnum():
            continue
        sql = ("INSERT INTO matrix_lan_bridge_runtime (bridge, state, health) "
               f"VALUES ('{bridge_id}', '{state}', '{health}') "
               "ON CONFLICT (bridge) DO UPDATE SET state=EXCLUDED.state, "
               "health=EXCLUDED.health, checked_at=now()")
        subprocess.run(compose + ["exec", "-T", "db", "psql", "-U", "postgres",
                       "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql], check=True)
    return int(failed)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    raise SystemExit(reconcile(parser.parse_args().runtime))
