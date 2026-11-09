"""Start (or find) the Video Studio host and remember where it listens.

One host per data directory, recorded in ``studio-host.json`` next to the plugin data —
the same convention Music Studio uses, so the desktop pane can find the app without
guessing a port.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE_FILE = "studio-host.json"
ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    base = os.environ.get("HERMES_PLUGIN_DATA_DIR")
    if base:
        return Path(base) / "hewn-video-studio"
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return home / "plugin-data" / "hewn-video-studio"


def state_path() -> Path:
    return data_dir() / STATE_FILE


def _alive(port: int, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _interpreter() -> str:
    """An interpreter that can import uvicorn (the Hermes runtime need not have it)."""
    for candidate in (sys.executable, "python3", "python3.13"):
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "-c", "import uvicorn, fastapi"],
                                   capture_output=True, timeout=20)
            if probe.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("no interpreter with uvicorn + fastapi found for the studio host")


def read_state() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def ensure_host(timeout: float = 45.0) -> dict:
    """Return ``{'url': ...}`` for a live host, starting one when needed."""
    state = read_state()
    port = int(state.get("port") or 0)
    if port and _alive(port):
        return {"url": f"http://127.0.0.1:{port}", "started": False}

    port = port or _free_port()
    env = dict(os.environ)
    env["VIDEO_STUDIO_PORT"] = str(port)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    dirpath = data_dir()
    dirpath.mkdir(parents=True, exist_ok=True)
    log = open(dirpath / "studio-host.log", "ab")
    proc = subprocess.Popen(
        [_interpreter(), "-m", "video_studio.studio_host"],
        cwd=str(ROOT), env=env, stdout=log, stderr=log,
        start_new_session=True,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _alive(port):
            state_path().write_text(json.dumps({"port": port, "pid": proc.pid}) + "\n")
            return {"url": f"http://127.0.0.1:{port}", "started": True, "pid": proc.pid}
        if proc.poll() is not None:
            raise RuntimeError(f"studio host exited early (rc {proc.returncode}); see {dirpath / 'studio-host.log'}")
        time.sleep(0.4)
    raise RuntimeError("studio host did not answer within the timeout")


if __name__ == "__main__":
    print(json.dumps(ensure_host(), indent=2))
