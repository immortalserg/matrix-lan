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

def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    specs = json.loads(MANIFEST.read_text())
    processes = {}
    retry = {}
    try:
        while not stopping:
            for name, spec in specs.items():
                if not name.isascii() or not name.isalnum():
                    raise ValueError("Invalid bridge identifier")
                process = processes.get(name)
                if process is not None and process.poll() is not None:
                    print(f"{name}: exited {process.returncode}", flush=True)
                    processes.pop(name)
                    retry[name] = time.monotonic() + 30
                    process = None
                if not spec.get("enabled", False):
                    continue
                if process is None and time.monotonic() >= retry.get(name, 0):
                    try:
                        processes[name] = subprocess.Popen(
                            spec["argv"], cwd=spec["cwd"],
                            env={**os.environ, **spec.get("env", {})},
                            start_new_session=True)
                    except OSError as exc:
                        print(f"{name}: startup failed: {exc}", flush=True)
                        retry[name] = time.monotonic() + 30
            time.sleep(1)
    finally:
        for process in processes.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 20
        for process in processes.values():
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

if __name__ == "__main__":
    main()
