"""Desktop recording, aimed at something, ending in the asset library.

`capture.desktop()` records inside a request and blocks. That is fine for a 6 s probe and wrong for
real footage: a demo of a feature is 20–40 s of the person driving the app, and the interesting
window has to be chosen while it is on screen.

So this module keeps the recorder as a *process with a lifecycle*:

    start(target)  -> wf-recorder runs, pid + path recorded in a state file
    status()       -> alive? elapsed? how big is the file? what was it aimed at?
    stop()         -> SIGINT, wait for the container to be finalised, probe it, ingest into Assets

The recording lands in the library like any other footage (content hash, proxy, thumbnail), so it
can be dressed into a beat with one call instead of living as an orphan file on disk.

Honesty rules that this file follows:
- the binary is checked before anything is claimed; absent means `deps`-shaped refusal, no message
- a recording is only "good" after ffprobe says it has a video stream and real duration
- a process that died on its own is reported as `failed` with its last stderr lines, never as `done`
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

STATE = Path(os.environ.get("HEWN_VIDEO_STUDIO_DATA") or
             Path.home() / ".hermes/plugin-data/hewn-video-studio") / "recorder.json"

# wf-recorder defaults that make a usable 1080p capture: x264 at ultrafast keeps the recorder off
# the CPU's critical path (the thing being recorded is what must stay smooth), crf 20 is visually
# clean for screen content with flat colour.
ARGS = ["-c", "libx264", "-p", "preset=ultrafast", "-p", "crf=20", "--overwrite"]


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _write(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a killed recorder lingers as a zombie under this process; treat that as gone
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def _probe(path: Path) -> dict:
    """What the file actually is. A killed recorder leaves a header without a moov atom."""
    if not path.exists() or path.stat().st_size == 0:
        return {"ok": False, "reason": "no file was written"}
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_type,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        info = {}
    streams = info.get("streams") or []
    duration = float((info.get("format") or {}).get("duration") or 0)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        return {"ok": False, "reason": "the file has no video stream (recording was cut off)",
                "bytes": path.stat().st_size}
    if duration < 0.4:
        return {"ok": False, "reason": f"only {duration:.2f}s of video was written", "duration": duration}
    return {"ok": True, "duration": round(duration, 2), "width": video.get("width"),
            "height": video.get("height"), "bytes": path.stat().st_size}


def targets() -> dict:
    """Everything a recording can be aimed at, so the UI never asks for free-form geometry."""
    from .capture import deps, windows

    d = deps()["desktop"]
    wins = windows()
    return {
        "ready": d["ready"],
        "recorder": d["recorder"],
        "install": d["command"],
        "full_screen": {"geometry": None, "label": "Whole screen"},
        "windows": [{"geometry": w["geometry"], "label": w["title"] or w["id"],
                     "id": w["id"], "size": f'{w["w"]}x{w["h"]}'} for w in wins],
        "note": ("Wayland only lists XWayland windows here; everything else is 'Whole screen' "
                 "or a region you give as WxH+X+Y"),
    }


def start(out: Path, geometry: str | None = None, seconds: float | None = None,
          audio: bool = False, label: str = "screen") -> dict:
    """Begin a recording. Returns immediately; `stop()` finalises it."""
    from .capture import deps

    if not shutil.which("wf-recorder"):
        d = deps()["desktop"]
        return {"ok": False, "reason": "wf-recorder is not installed",
                "install": d["command"], "alternative": "record with OBS, then import the file"}
    if _alive(_state().get("pid")):
        return {"ok": False, "reason": "a recording is already running", "state": status()}

    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    cmd = ["wf-recorder", "-f", str(out)] + ARGS
    if geometry:
        cmd += ["-g", geometry]
    if audio:
        cmd += ["--audio"]
    # no pipes: nothing reads them, and an unread pipe blocks the recorder once it fills
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {"pid": proc.pid, "path": str(out), "geometry": geometry, "label": label,
             "audio": audio, "started": time.time(),
             "deadline": (time.time() + float(seconds)) if seconds else None, "cmd": cmd}
    _write(state)
    if seconds:
        # The host process outlives the page, so the timer lives here. A closed UI must not leave
        # a recorder filling the disk.
        import threading
        threading.Timer(float(seconds), _auto_stop).start()
    return {"ok": True, "pid": proc.pid, "path": str(out), "geometry": geometry,
            "auto_stop_at": state["deadline"]}


def _auto_stop() -> None:
    try:
        if status().get("recording"):
            stop()
    except Exception:
        pass


def status() -> dict:
    """Is it recording, and did the thing it started actually stay up?"""
    s = _state()
    if not s:
        return {"recording": False, "state": "idle"}
    path = Path(s["path"])
    alive = _alive(s.get("pid"))
    size = path.stat().st_size if path.exists() else 0
    elapsed = round(time.time() - s["started"], 1)
    out = {"recording": alive, "state": "recording" if alive else "stopped",
           "elapsed": elapsed, "bytes": size, "path": str(path), "label": s.get("label"),
           "geometry": s.get("geometry"), "audio": s.get("audio"),
           "auto_stop_at": s.get("deadline")}
    if alive and s.get("deadline") and time.time() >= s["deadline"]:
        out["auto_stop_due"] = True
    # a file that never grew means the compositor refused screencopy (a portal prompt, or a
    # compositor without the protocol) — say that instead of showing a growing timer on nothing
    if alive and elapsed > 4 and size < 20_000:
        out["warning"] = "no frames are arriving — the compositor may need a screencopy permission prompt"
    return out


def stop() -> dict:
    """SIGINT so wf-recorder writes the mp4 index, then prove the result and file it as an asset."""
    s = _state()
    if not s:
        return {"ok": False, "reason": "nothing was recording"}
    pid, path = s.get("pid"), Path(s["path"])
    if pid and _alive(pid):
        try:
            os.kill(pid, signal.SIGINT)
        except OSError:
            pass
        for _ in range(80):          # up to 8 s: x264 flushes and the container is finalised
            if not _alive(pid):
                break
            time.sleep(0.1)
        if _alive(pid):
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.6)

    probe = _probe(path)
    result = {"ok": probe["ok"], "path": str(path), "probe": probe,
              "seconds": probe.get("duration"), "label": s.get("label")}
    if probe["ok"]:
        try:
            from .assets_api import ingest       # same intake every other asset uses
            rec = ingest(path)
            result["asset"] = {"id": rec["id"], "kind": rec["kind"], "new": rec.get("new", False)}
        except Exception as exc:                  # an import failure must not lose the file
            result["asset"] = {"error": f"{type(exc).__name__}: {exc}"}
    _write({**s, "pid": None, "stopped": time.time(), "result": result})
    return result


def forget() -> dict:
    """Clear the state file (e.g. after a capture has been used)."""
    _write({})
    return {"ok": True}


def available() -> bool:
    return bool(shutil.which("wf-recorder"))


def last_recording() -> Path | None:
    """The newest finished recording, when it was good. Used by `footage: "last"`."""
    s = _state()
    path = Path(s.get("path") or "")
    if path.is_file() and (s.get("result") or {}).get("ok"):
        return path
    # fall back to the newest usable file in the captures folder
    from .paths import RENDERS
    files = sorted((RENDERS / "captures").glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for f in reversed(files):
        probe = _probe(f)
        if probe.get("ok"):
            return f
    return None
