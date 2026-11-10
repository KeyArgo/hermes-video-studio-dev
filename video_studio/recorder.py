"""Desktop recording, aimed and configured the way a person actually records a demo.

`capture.desktop()` records inside a request and blocks. That is fine for a 6 s probe and wrong
for footage: a demo of a feature is 20–40 s of somebody driving the app, and the take has to be
aimed at the right window, at the right frame rate, with or without sound, ending in the library.

So this module keeps the recorder as a process with a lifecycle:

    start(...)  -> wf-recorder runs with the options asked for; pid + path + options recorded
    status()    -> alive? elapsed? how big is the file? what was it aimed at?
    stop()      -> SIGINT, wait for the container to be finalised, probe it, ingest into Assets

Option mapping is the part that must not be guessed. wf-recorder takes:

    -f FILE                 output (container from the extension)
    -g "x,y WxH"            region — NOT ffmpeg's `WxH+x+y`; a window's wmctrl geometry must be
                            converted, which is what `wf_geometry()` does
    -r N                    hard constant frame rate (duplicates frames to reach it)
    -D / --no-damage        capture every frame, not only when the screen changes
    -c CODEC -p k=v         video codec and its parameters (repeatable)
    -a[=DEVICE]             audio, optionally a named PulseAudio/PipeWire source
    -C CODEC -R RATE        audio codec and sample rate
    -o OUTPUT / -L          monitor selection / list monitors
    -y                      overwrite

Honesty rules:
- the binary is checked before anything is claimed; absent means a refusal that names the install
- a take is only "good" after ffprobe says it has a video stream and real duration
- a process that died on its own is reported as failed with the reason, never as done
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

# quality presets: screen content is flat colour and text, so ultrafast at crf 20 is visually
# clean, and the recorder stays off the CPU of the thing being recorded (which must stay smooth).
QUALITY = {
    "draft":    {"preset": "ultrafast", "crf": "26"},
    "standard": {"preset": "ultrafast", "crf": "20"},
    "high":     {"preset": "superfast", "crf": "16"},
}
DEFAULTS = {"quality": "standard", "fps": 60, "smooth": True, "audio": False,
            "audio_device": None, "output": None, "codec": "libx264"}


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


def _remembered() -> dict:
    """Options persist between takes: nobody wants to re-pick 60 fps every time."""
    saved = _state().get("options") or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"      # a zombie is gone for our purposes
    except OSError:
        return True


def wf_geometry(rect: str | None) -> str | None:
    """Convert a window rectangle to the form wf-recorder wants.

    Accepts `WxH+X+Y` (wmctrl) or `x,y WxH` (already wf-recorder's own). Returns None for the
    whole screen. A wrong geometry is not a cosmetic bug: wf-recorder refuses to start.
    """
    if not rect:
        return None
    rect = rect.strip()
    if " " in rect:                                  # already `x,y WxH`
        return rect
    try:
        parts = rect.split("+")
        w, h = parts[0].split("x")
        return f"{int(parts[1])},{int(parts[2])} {int(w)}x{int(h)}"
    except (IndexError, ValueError):
        raise ValueError(f"not a rectangle: {rect!r} (expected 'WxH+X+Y' or 'x,y WxH')")


def capabilities() -> dict:
    """What this machine can record, and the choices that follow from it.

    Everything here is observed: monitors come from `wf-recorder -L`, microphones from `pactl`,
    and the region picker needs `slurp`. A missing one disables exactly one control.
    """
    from .capture import deps

    d = deps()["desktop"]
    ready = bool(shutil.which("wf-recorder"))
    monitors, version = [], None
    if ready:
        v = subprocess.run(["wf-recorder", "--version"], capture_output=True, text=True, timeout=10)
        text = (v.stdout or v.stderr or "").strip()
        version = text.splitlines()[0] if text else None
        out = subprocess.run(["wf-recorder", "-L"], capture_output=True, text=True, timeout=10)
        monitors = [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]
    sources = []
    if shutil.which("pactl"):
        s = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True,
                           timeout=10)
        for line in (s.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                sources.append({"id": parts[0], "name": parts[1]})
    return {
        "ready": ready,
        "recorder": shutil.which("wf-recorder"),
        "version": version,
        "install": d["command"],
        "monitors": monitors,
        "audio_sources": sources,
        "region_picker": bool(shutil.which("slurp")),
        "qualities": list(QUALITY),
        "framerate": [30, 60, 120],
        "defaults": _remembered(),
        "why": {
            "fps": "60 matches the display, so motion is smooth in the cut; 30 halves the file",
            "smooth": "captures every frame. Without it wf-recorder only sends frames when the "
                      "screen changes, which reads as stutter once it is in a 30 fps cut",
            "quality": "draft for timing a take, high for the shot that ships",
            "audio": "system sound and microphone together, or one named source",
        },
    }


def targets() -> dict:
    """Everything a recording can be aimed at: whole screen, a window, a region, a monitor."""
    from .capture import windows

    caps = capabilities()
    wins = windows()
    return {
        "ready": caps["ready"],
        "recorder": caps["recorder"],
        "version": caps["version"],
        "install": caps["install"],
        "monitors": caps["monitors"],
        "region_picker": caps["region_picker"],
        "full_screen": {"geometry": None, "label": "Whole screen"},
        "windows": [{"geometry": w["geometry"], "label": w["title"] or w["id"],
                     "id": w["id"], "size": f'{w["w"]}x{w["h"]}'} for w in wins],
        "note": ("On Wayland only XWayland windows can be listed here; anything else is "
                 "'Whole screen', a monitor, or a region you draw"),
    }


def _argv(out: Path, opts: dict, geometry: str | None) -> list[str]:
    q = QUALITY.get(opts["quality"], QUALITY["standard"])
    cmd = ["wf-recorder", "-f", str(out), "-c", opts["codec"],
           "-p", f"preset={q['preset']}", "-p", f"crf={q['crf']}", "-y"]
    if opts.get("fps"):
        cmd += ["-r", str(int(opts["fps"]))]
    if opts.get("smooth"):
        cmd += ["--no-damage"]          # every frame, so the cut does not inherit VFR stutter
    if geometry:
        cmd += ["-g", geometry]
    if opts.get("audio"):
        dev = opts.get("audio_device")
        cmd += [f"--audio={dev}" if dev else "--audio", "-C", "aac", "-R", "48000"]
    if opts.get("output"):
        cmd += ["-o", str(opts["output"])]
    return cmd


def start(out: Path, geometry: str | None = None, seconds: float | None = None,
          audio: bool | None = None, label: str = "screen", film: str | None = None,
          beat: str | None = None, **opts) -> dict:
    """Begin a recording with the options asked for. Returns at once; `stop()` finalises it."""
    from .capture import deps

    if not shutil.which("wf-recorder"):
        d = deps()["desktop"]
        return {"ok": False, "reason": "wf-recorder is not installed",
                "install": d["command"], "alternative": "record with OBS, then import the file"}
    if _alive(_state().get("pid")):
        return {"ok": False, "reason": "a recording is already running", "state": status()}

    merged = _remembered()
    merged.update({k: v for k, v in opts.items() if k in DEFAULTS and v is not None})
    if audio is not None:
        merged["audio"] = bool(audio)
    merged["geometry"] = wf_geometry(geometry)

    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    cmd = _argv(out, merged, merged["geometry"])
    # no pipes: nothing reads them, and an unread pipe blocks the recorder once it fills
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = {"pid": proc.pid, "path": str(out), "geometry": merged["geometry"], "label": label,
             "audio": merged["audio"], "started": time.time(), "options": merged,
             "film": film, "beat": beat,
             "deadline": (time.time() + float(seconds)) if seconds else None, "cmd": cmd}
    _write(state)
    if seconds:
        # The host process outlives the page, so the timer lives here. A closed UI must not leave
        # a recorder filling the disk.
        import threading
        threading.Timer(float(seconds), _auto_stop).start()
    return {"ok": True, "pid": proc.pid, "path": str(out), "geometry": merged["geometry"],
            "options": merged, "film": film, "beat": beat,
            "auto_stop_at": state["deadline"], "cmd": " ".join(cmd)}


def _auto_stop() -> None:
    try:
        if status().get("recording"):
            stop()
    except Exception:
        pass


def status() -> dict:
    """Is it recording, and did the thing it started actually stay up?"""
    s = _state()
    if not s.get("path") and not s.get("pid"):
        return {"recording": False, "state": "idle", "options": s.get("options")}
    path = Path(s["path"])
    alive = _alive(s.get("pid"))
    size = path.stat().st_size if path.exists() else 0
    elapsed = round(time.time() - s["started"], 1)
    out = {"recording": alive, "state": "recording" if alive else "stopped",
           "elapsed": elapsed, "bytes": size, "path": str(path), "label": s.get("label"),
           "geometry": s.get("geometry"), "audio": s.get("audio"),
           "options": s.get("options"), "auto_stop_at": s.get("deadline"),
           "film": s.get("film"), "beat": s.get("beat"), "result": s.get("result")}
    if alive and s.get("deadline") and time.time() >= s["deadline"]:
        out["auto_stop_due"] = True
    # a file that never grew means the compositor refused screencopy (a portal prompt, or a
    # compositor without the protocol) — say that instead of showing a growing timer on nothing
    # mp4 buffers, so a growing-but-small file is normal; only an *empty* one means nothing
    # is arriving (a refused screencopy, or a portal prompt waiting off-screen)
    if alive and elapsed > 4 and size < 1000:
        out["warning"] = "no frames are arriving — the compositor may need a screencopy permission prompt"
    return out


def _probe(path: Path) -> dict:
    """What the file actually is. A killed recorder leaves a header without a moov atom."""
    if not path.exists() or path.stat().st_size == 0:
        return {"ok": False, "reason": "no file was written"}
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_type,width,height,r_frame_rate",
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
            "height": video.get("height"), "fps": video.get("r_frame_rate"),
            "bytes": path.stat().st_size}


def stop() -> dict:
    """SIGINT so wf-recorder writes the mp4 index, then prove the result and file it as an asset."""
    s = _state()
    if not s.get("path") and not s.get("pid"):
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


def takes(limit: int = 20) -> list[dict]:
    """Every take recorded here, newest first, with the numbers a person picks one by."""
    try:
        from .films_api import RENDERS          # one definition of the workspace, not two
    except Exception:
        return []
    folder = RENDERS / "captures"
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        probe = _probe(f)
        out.append({"name": f.stem, "path": str(f), "mtime": int(f.stat().st_mtime),
                    "seconds": probe.get("duration"), "width": probe.get("width"),
                    "height": probe.get("height"), "fps": probe.get("fps"),
                    "ok": probe.get("ok"), "reason": probe.get("reason"),
                    "bytes": f.stat().st_size})
    return out


def forget() -> dict:
    """Clear the take, keeping the remembered options."""
    _write({"options": _state().get("options") or _remembered()})
    return {"ok": True}


def last_recording() -> Path | None:
    """The newest finished recording, when it was good. Used by `footage: "last"`."""
    s = _state()
    path = Path(s.get("path") or "")
    if path.is_file() and (s.get("result") or {}).get("ok"):
        return path
    good = [t for t in takes() if t["ok"]]
    return Path(good[0]["path"]) if good else None


def available() -> bool:
    return bool(shutil.which("wf-recorder"))
