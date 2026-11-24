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

def publish_states(specs, processes, retry, desired, shutting_down=False):
    dsn = os.environ.get("MATRIX_BRIDGES_DSN")
    if not dsn:
        return
    import psycopg2
    from contextlib import closing
    rows = []
    for name, spec in specs.items():
        process = processes.get(name)
        if not spec.get("ready", False):
            state = "not_prepared"
        elif shutting_down or not desired.get(name, spec.get("enabled", False)):
            state = "stopped"
        elif process is not None and process.poll() is None:
            state = "running"
        elif name in retry:
            state = "retrying"
        else:
            state = "starting"
        rows.append((name, state, "unknown" if state == "running" else "not_running"))
    with closing(psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000")) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO matrix_lan_bridge_runtime (bridge, state, health, checked_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (bridge) DO UPDATE SET state = EXCLUDED.state,
                    health = EXCLUDED.health, checked_at = EXCLUDED.checked_at""", rows)

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
    next_publish = 0
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
            if time.monotonic() >= next_publish:
                next_publish = time.monotonic() + 5
                try:
                    publish_states(specs, processes, retry, desired)
                except Exception as exc:
                    print(f"State publication failed: {type(exc).__name__}", flush=True)
            time.sleep(1)
    finally:
        for process in processes.values():
            terminate(process)
        try:
            publish_states(specs, {}, {}, desired, shutting_down=True)
        except Exception as exc:
            print(f"Shutdown publication failed: {type(exc).__name__}", flush=True)

if __name__ == "__main__":
    main()
