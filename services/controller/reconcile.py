#!/usr/bin/env python3
"""Run on the trusted Docker host, never in the public HTTP service."""
import argparse
import json
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
    return int(failed)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    raise SystemExit(reconcile(parser.parse_args().runtime))
