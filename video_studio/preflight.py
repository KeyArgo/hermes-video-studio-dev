"""What this install can actually do, in the order a new user hits it.

An install that fails tells you nothing; an install that reports four lines tells you exactly what
to type. Every check here is observed on this machine — versions, paths, whether a binary answers —
and every failing check carries the command that fixes it.

Used by `GET /api/preflight` (the studio shows it in Production) and by the tool, so an agent can
answer "why is this not working" without reading a traceback.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_PY = ("fastapi", "uvicorn")
OPTIONAL_PY = {
    "numpy": "beat phase detection (cut to the music)",
    "opentimelineio": "interchange export (OTIO)",
}


def _workspace() -> Path:
    for env in ("VIDEO_STUDIO_WORKSPACE", "VIDEO_STUDIO_MOTION"):
        if os.environ.get(env):
            return Path(os.environ[env]).expanduser()
    for candidate in (Path.home() / "VideoStudio", Path.home() / "Development" / "VideoStudio"):
        if (candidate / "films").is_dir():
            return candidate
    return Path.home() / "VideoStudio"


def _py_dep(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _binary(name: str) -> str | None:
    return shutil.which(name)


def _version(cmd: list[str], timeout: float = 12.0) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        text = (r.stdout or r.stderr or "").strip().splitlines()
        return text[0][:120] if text else None
    except Exception:
        return None


def check() -> dict:
    py_ok = {name: _py_dep(name) for name in REQUIRED_PY}
    optional = {name: {"present": _py_dep(name), "enables": why} for name, why in OPTIONAL_PY.items()}
    missing_py = [k for k, v in py_ok.items() if not v]

    ffmpeg = _binary("ffmpeg")
    melt = _binary("melt")
    node = _binary("npx") or _binary("node")
    recorder = _binary("wf-recorder")

    rows = [
        {"id": "python", "ok": sys.version_info >= (3, 10),
         "detail": f"python {sys.version.split()[0]}", "fix": "install Python 3.10 or newer"},
        {"id": "python-deps", "ok": not missing_py,
         "detail": ("fastapi + uvicorn import" if not missing_py
                    else "missing: " + ", ".join(missing_py)),
         "fix": f"{sys.executable} -m pip install -r requirements.txt"},
        {"id": "ffmpeg", "ok": bool(ffmpeg),
         "detail": _version([ffmpeg or "ffmpeg", "-version"]) if ffmpeg else "not found",
         "fix": "install ffmpeg (zypper install ffmpeg / apt install ffmpeg)"},
        {"id": "melt", "ok": bool(melt),
         "detail": _version([melt or "melt", "-version"]) if melt else "not found — assembly falls back to ffmpeg",
         "fix": "install MLT (zypper install libmlt7 melt) for the multi-track cut"},
        {"id": "hyperframes", "ok": bool(node),
         "detail": ("npx available — shots render via `npx hyperframes`" if node else "no node/npx"),
         "fix": "install Node 18+ so `npx hyperframes` can render typed shots"},
        {"id": "recorder", "ok": bool(recorder), "optional": True,
         "detail": recorder or "wf-recorder not installed — desktop capture is unavailable",
         "fix": "sudo zypper install wf-recorder (optional: browser capture needs nothing)"},
        {"id": "workspace", "ok": True,
         "detail": str(_workspace()),
         "fix": "set VIDEO_STUDIO_WORKSPACE to choose where films and renders are kept"},
    ]
    blocking = [r for r in rows if not r["ok"] and not r.get("optional")]
    return {
        "ok": not blocking,
        "blocking": [r["id"] for r in blocking],
        "rows": rows,
        "optional_deps": optional,
        "python": sys.executable,
        "note": ("A fresh install needs ffmpeg before anything renders; every other check names "
                 "the command that fixes it."),
    }
