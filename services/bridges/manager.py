"""Container-local supervisor. No Docker socket or host commands."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

MANIFEST = Path("/etc/matrix-bridges/processes.json")
stopping = False

def stop(signum, frame):
    global stopping
    stopping = True

def terminate(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()

def desired_states():
    dsn = os.environ.get("MATRIX_BRIDGES_DSN")
    if not dsn:
        return {}
    import psycopg2
    from contextlib import closing
    with closing(psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT bridge, desired_enabled FROM matrix_lan_bridge_settings")
            return dict(cur.fetchall())

def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    specs = json.loads(MANIFEST.read_text())
    for name in specs:
        if not name.isascii() or not name.isalnum():
            raise ValueError("Invalid bridge identifier")
    processes = {}
    retry = {}
    desired = {}
    next_poll = 0
    try:
        while not stopping:
            now = time.monotonic()
            if now >= next_poll:
                next_poll = now + 5
                try:
                    desired = desired_states()
                except Exception as exc:
                    # Retain last known state during database outages.
                    print(f"State query failed: {type(exc).__name__}", flush=True)
            for name, spec in specs.items():
                process = processes.get(name)
                if process is not None and process.poll() is not None:
                    print(f"{name}: exited {process.returncode}", flush=True)
                    terminate(process)
                    processes.pop(name)
                    retry[name] = time.monotonic() + 30
                    process = None
                enabled = spec.get("ready", False) and desired.get(name, spec.get("enabled", False))
                if not enabled:
                    if process is not None:
                        terminate(process)
                        processes.pop(name)
                    retry.pop(name, None)
                    continue
                if process is None and time.monotonic() >= retry.get(name, 0):
                    try:
                        env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ", "SSL_CERT_FILE", "PLAYWRIGHT_BROWSERS_PATH") if k in os.environ}
                        env.update(spec.get("env", {}))
                        env["HOME"] = spec["cwd"]
                        processes[name] = subprocess.Popen(
                            spec["argv"], cwd=spec["cwd"], env=env,
                            start_new_session=True)
                    except OSError as exc:
                        print(f"{name}: startup failed: {type(exc).__name__}", flush=True)
                        retry[name] = time.monotonic() + 30
            time.sleep(1)
    finally:
        for process in processes.values():
            terminate(process)

if __name__ == "__main__":
    main()
