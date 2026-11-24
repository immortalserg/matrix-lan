"""Container-local supervisor. No Docker socket or host commands."""
import json
import re
import socket
import threading
import logging
from logging.handlers import RotatingFileHandler
import http.client
from concurrent.futures import ThreadPoolExecutor
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
    if hasattr(process, 'log_reader'):
        process.log_reader.join(timeout=3)

# Fail closed for recognizable credential-bearing lines. Arbitrary message
# content cannot be reliably identified by pattern matching.
SENSITIVE = re.compile(
    r"(?i)(authorization|bearer\s|access[_-]?token|refresh[_-]?token|"
    r"as[_-]?token|hs[_-]?token|api[_-]?hash|api[_-]?key|password|passwd|"
    r"cookie|secret|session[_-]?(id|token|key)|private[_-]?key|"
    r"postgres(?:ql)?://|otpauth://|mxc://|qr[_ -]?code|"
    r"login[_ -]?code|verification[_ -]?code)"
)

def redact_line(raw):
    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    if SENSITIVE.search(line):
        return "[REDACTED: potential credential]"
    return line

def drain_output(name, stream, handler):
    try:
        with stream:
            while True:
                chunk = stream.readline(16384)
                if not chunk:
                    break
                if len(chunk) == 16384 and not chunk.endswith(b"\n"):
                    while chunk and not chunk.endswith(b"\n"):
                        chunk = stream.readline(16384)
                    message = "[REDACTED: oversized log line]"
                else:
                    message = redact_line(chunk)
                record = logging.LogRecord(name, logging.INFO, "", 0,
                    message, (), None)
                handler.handle(record)
    finally:
        handler.close()

def launch(name, spec, env):
    directory = Path("/var/log/matrix-bridges")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / (name + ".log"),
                                  maxBytes=5 * 1024 * 1024, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    try:
        process = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    except BaseException:
        handler.close()
        raise
    reader = threading.Thread(target=drain_output,
                              args=(name, process.stdout, handler), daemon=True)
    reader.start()
    process.log_reader = reader
    return process

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

def telegram_credentials():
    dsn = os.environ.get("MATRIX_BRIDGES_DSN")
    if not dsn:
        return None
    import psycopg2
    from contextlib import closing
    with closing(psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT api_id,api_hash,updated_at FROM matrix_lan_telegram_credentials WHERE singleton")
            return cur.fetchone()

def acknowledge_telegram(credentials):
    import psycopg2
    from contextlib import closing
    with closing(psycopg2.connect(os.environ["MATRIX_BRIDGES_DSN"], connect_timeout=3,
                                  options="-c statement_timeout=3000")) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE matrix_lan_telegram_credentials SET applied_at=now()
                    WHERE singleton AND api_id=%s AND api_hash=%s AND updated_at=%s""", credentials)

def telegram_configured(spec):
    import yaml
    try:
        config = yaml.safe_load((Path(spec["cwd"]) / "config.yaml").read_text(encoding="utf-8"))
        network = config["network"]
        api_id = network.get("api_id")
        api_hash = network.get("api_hash")
        return (type(api_id) is int and 0 < api_id <= 2147483647
                and isinstance(api_hash, str)
                and re.fullmatch(r"[a-fA-F0-9]{32}", api_hash) is not None)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        return False

def apply_telegram_credentials(spec, credentials):
    import tempfile
    import yaml
    path = Path(spec["cwd"]) / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    api_id, api_hash = credentials
    if not 0 < api_id <= 2147483647 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("Invalid Telegram credentials")
    network = config["network"]
    if network.get("api_id") == api_id and network.get("api_hash") == api_hash:
        return
    network.update(api_id=api_id, api_hash=api_hash)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".telegram-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(config, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def restart_requests():
    dsn = os.environ.get("MATRIX_BRIDGES_DSN")
    if not dsn:
        return {}
    import psycopg2
    from contextlib import closing
    with closing(psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT bridge,generation FROM matrix_lan_bridge_restart WHERE generation > applied_generation")
            return dict(cur.fetchall())

def acknowledge_restart(name, generation):
    import psycopg2
    from contextlib import closing
    with closing(psycopg2.connect(os.environ["MATRIX_BRIDGES_DSN"], connect_timeout=3,
                                  options="-c statement_timeout=3000")) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE matrix_lan_bridge_restart
                    SET applied_generation=GREATEST(applied_generation,%s)
                    WHERE bridge=%s AND generation >= %s""", (generation,name,generation))

def probe(spec):
    """Probe only the local bridge listener, never follow redirects."""
    port = spec.get("port")
    if type(port) is not int or not 1024 <= port <= 65535:
        return "unknown"
    path = spec.get("health_path")
    try:
        if not path:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return "tcp_reachable"
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return "healthy" if 200 <= response.status < 300 else "unhealthy"
        finally:
            conn.close()
    except (OSError, http.client.HTTPException):
        return "unhealthy"

def publish_states(specs, processes, retry, desired, shutting_down=False):
    dsn = os.environ.get("MATRIX_BRIDGES_DSN")
    if not dsn:
        return
    import psycopg2
    from contextlib import closing
    rows = []
    active = {name: spec for name, spec in specs.items()
              if not shutting_down and spec.get("ready", False)
              and name in processes and processes[name].poll() is None}
    with ThreadPoolExecutor(max_workers=16) as pool:
        health = dict(zip(active, pool.map(probe, active.values())))
    for name, spec in specs.items():
        process = processes.get(name)
        if not spec.get("ready", False):
            state = "not_prepared"
        elif shutting_down or not desired.get(name, spec.get("enabled", False)):
            state = "stopped"
        elif process is not None and process.poll() is None:
            state = "running"
        elif name == "telegram" and not telegram_configured(spec):
            state = "configuration_required"
        elif name in retry:
            state = "retrying"
        else:
            state = "starting"
        rows.append((name, state, health.get(name, "unknown") if state == "running" else "not_running"))
    with closing(psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000")) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO matrix_lan_bridge_runtime (bridge, state, health, checked_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (bridge) DO UPDATE SET state = EXCLUDED.state,
                    health = EXCLUDED.health, checked_at = EXCLUDED.checked_at""", rows)

def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    specs = json.loads(MANIFEST.read_text())
    for name in specs:
        if not name.isascii() or not name.isalnum():
            raise ValueError("Invalid bridge identifier")
    processes = {}
    retry = {}
    desired = {}
    pending = {}
    telegram = None
    telegram_applied = None
    attempted = {}
    completed = {}
    next_poll = 0
    next_publish = 0
    try:
        while not stopping:
            now = time.monotonic()
            if now >= next_poll:
                next_poll = now + 5
                try:
                    desired = desired_states()
                    pending = restart_requests()
                    telegram = telegram_credentials()
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
                if name == "telegram" and telegram is not None and telegram != telegram_applied:
                    if process is not None:
                        terminate(process)
                        processes.pop(name)
                        process = None
                    try:
                        apply_telegram_credentials(spec, telegram[:2])
                        acknowledge_telegram(telegram)
                        telegram_applied = telegram
                        retry.pop(name, None)
                    except Exception as exc:
                        print(f"telegram: configuration failed: {type(exc).__name__}", flush=True)
                        continue
                if name == "telegram" and not telegram_configured(spec):
                    if process is not None:
                        terminate(process)
                        processes.pop(name)
                    continue
                generation = pending.get(name, 0)
                if generation > attempted.get(name, 0):
                    if process is not None:
                        terminate(process)
                        processes.pop(name)
                        process = None
                    retry.pop(name, None)
                    attempted[name] = generation
                if process is None and time.monotonic() >= retry.get(name, 0):
                    try:
                        env = {k: os.environ[k] for k in ("PATH", "LANG", "TZ", "SSL_CERT_FILE", "PLAYWRIGHT_BROWSERS_PATH") if k in os.environ}
                        env.update(spec.get("env", {}))
                        env["HOME"] = spec["cwd"]
                        processes[name] = launch(name, spec, env)
                        if generation:
                            completed[name] = generation
                    except OSError as exc:
                        print(f"{name}: startup failed: {type(exc).__name__}", flush=True)
                        retry[name] = time.monotonic() + 30
            for name, generation in list(completed.items()):
                try:
                    acknowledge_restart(name, generation)
                    completed.pop(name)
                    if pending.get(name) == generation:
                        pending.pop(name)
                except Exception as exc:
                    print(f"{name}: restart acknowledgement failed: {type(exc).__name__}", flush=True)
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
