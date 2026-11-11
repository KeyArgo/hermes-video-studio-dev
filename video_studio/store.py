"""Project store — one directory per cut, on disk, human-readable.

Nothing is a database. A project is a folder:

    <root>/<project-id>/
        project.json      source + ops + meta (the whole edit)
        renders/*.mp4     every render, kept, never overwritten
        waveform.png      cached peaks for the UI

Root: $HEWN_VIDEO_STUDIO_HOME, else $XDG_STATE_HOME/video-studio,
else ~/.local/state/video-studio.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import edl


def root() -> Path:
    env = os.environ.get("HEWN_VIDEO_STUDIO_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "video-studio"


def projects_dir() -> Path:
    p = root() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "cut")[:60]


def project_dir(pid: str) -> Path:
    """Resolve a project id to its directory, refusing anything that escapes root."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", pid or ""):
        raise ValueError("invalid project id")
    d = projects_dir() / pid
    if not d.is_dir():
        raise FileNotFoundError(pid)
    return d


def exists(pid: str) -> bool:
    try:
        project_dir(pid)
        return True
    except (ValueError, FileNotFoundError):
        return False


def unique_id(base: str) -> str:
    pid, n = slug(base), 2
    while (projects_dir() / pid).exists():
        pid = f"{slug(base)}-{n}"
        n += 1
    return pid


def create(source: str, name: str | None = None, entry: str = "",
           accent: str = "#3b82f6") -> tuple[str, "edl.Project"]:
    src = os.path.abspath(os.path.expanduser(source))
    if not os.path.exists(src):
        raise FileNotFoundError(f"source not found: {src}")
    info = edl.probe(src)
    if not info["duration"]:
        raise ValueError("source has no video stream")
    pid = unique_id(name or Path(src).stem)
    d = projects_dir() / pid
    (d / "renders").mkdir(parents=True)
    p = edl.Project(src, pid)
    p.meta = {"entry": entry, "accent": accent, "created": int(time.time()),
              "updated": int(time.time()), "title": name or Path(src).stem}
    save(pid, p)
    return pid, p


def load(pid: str) -> "edl.Project":
    d = project_dir(pid)
    f = d / "project.json"
    if not f.exists():
        raise FileNotFoundError(pid)
    data = json.loads(f.read_text())
    p = edl.Project(data["source"], data.get("name") or pid)
    p.ops = data.get("ops", [])
    p.meta = data.get("meta", {})
    return p


def save(pid: str, p: "edl.Project") -> None:
    d = project_dir(pid)
    p.meta = {**(p.meta or {}), "updated": int(time.time())}
    payload = {"name": p.name, "source": p.source, "ops": p.ops, "meta": p.meta}
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(d / "project.json")


def list_projects() -> list[dict]:
    out = []
    for d in sorted(projects_dir().iterdir()):
        if not d.is_dir() or not (d / "project.json").exists():
            continue
        try:
            data = json.loads((d / "project.json").read_text())
            p = edl.Project(data["source"], data.get("name") or d.name)
            p.ops = data.get("ops", [])
            p.meta = data.get("meta", {})
            tl, caps = p.timeline()
            renders = sorted((d / "renders").glob("*.mp4"))
            out.append({
                "id": d.name,
                "title": p.meta.get("title") or d.name,
                "entry": p.meta.get("entry", ""),
                "accent": p.meta.get("accent", "#3b82f6"),
                "source": p.source,
                "source_duration": round(edl.probe(p.source)["duration"], 2),
                "duration": round(sum(s["duration"] for s in tl), 2),
                "segments": len(tl),
                "ops": len(p.ops),
                "captions": len(caps),
                "renders": [r.name for r in renders],
                "updated": p.meta.get("updated", 0),
            })
        except Exception:  # noqa: BLE001 — one broken project must not hide the rest
            continue
    return out


def render_path(pid: str, name: str) -> Path:
    d = project_dir(pid) / "renders"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.mp4", name or ""):
        raise ValueError("invalid render name")
    f = d / name
    if not f.exists():
        raise FileNotFoundError(name)
    return f
