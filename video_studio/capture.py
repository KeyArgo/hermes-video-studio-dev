"""Real footage of a running app: scripted, reproducible, 1080p30.

The weakness this fixes is structural, not cosmetic. A film made only of HTML typed by a model
looks like HTML typed by a model: nothing on screen is a real product doing a real thing. A
capture of the actual interface — buttons being pressed, panels filling with data — cannot be
invented by a language model, which is exactly why it reads as proof.

Scripts are lists of steps, so a demo is repeatable and reviewable in a diff:

    [{"click": ".room[data-room='assets']", "after": 1200},
     {"wait": 600},
     {"scroll": 300},
     {"mouse": [900, 400]}]

Output is a webm from the browser, then a 1080p30 H.264 mp4 with no audio (the cut owns sound).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

NODE = "node"
CAPTURE_JS = Path(__file__).with_name("capture.js")


def record(url: str, steps: list[dict], out: Path, width: int = 1920, height: int = 1080,
           fps: int = 30, timeout: float = 300.0) -> dict:
    """Drive a page through `steps` while recording, then normalise to H.264 1080p30."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        spec = json.dumps({"url": url, "steps": steps, "out": tmp,
                           "width": width, "height": height})
        r = subprocess.run([NODE, str(CAPTURE_JS), spec], capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"capture failed: {(r.stdout + r.stderr)[-600:]}")
        try:
            payload = json.loads(r.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            raise RuntimeError(f"capture produced no result: {r.stdout[-400:]}") from None
        webm = Path(payload["video"])
        if not webm.is_file():
            raise RuntimeError("capture wrote no video")
        vf = f"scale={width}:{height}:flags=lanczos,fps={fps},setsar=1"
        conv = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(webm), "-vf", vf,
                               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                              capture_output=True, text=True)
        if conv.returncode != 0:
            raise RuntimeError(f"encode failed: {conv.stderr[-400:]}")
    seconds = _seconds(out)
    return {"output": str(out), "seconds": seconds, "width": width, "height": height,
            "fps": fps, "steps": len(steps), "console_errors": payload.get("consoleErrors") or []}


def _seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return round(float(out or 0), 2)


def studio_walkthrough(url: str, out: Path) -> dict:
    """The demo most films need: the product being used, in the order a stranger can follow.

    Kept here rather than in the UI so it can be re-shot identically after any change, and so a
    regression in the interface shows up as a failed capture rather than a bad video. The player
    is started on purpose: footage of an interface sitting still proves nothing; footage of it
    running the work does.
    """
    steps = [
        {"wait": 900},
        {"click": ".room[data-room='films']", "after": 1200},
        {"eval": "(() => { const v = document.getElementById('player'); if (v) { v.muted = true; v.currentTime = 3.2; v.play().catch(() => {}); } })()", "after": 2800},
        {"wait": 1600},
        {"click": ".room[data-room='assets']", "after": 1600},
        {"wait": 700},
        {"click": ".room[data-room='script']", "after": 1600},
        {"wait": 700},
        {"click": ".room[data-room='music']", "after": 1800},
        {"wait": 900},
        {"click": ".room[data-room='generate']", "after": 1600},
        {"wait": 900},
    ]
    return record(url, steps, out)


def windows() -> list[dict]:
    """Desktop windows with geometry, so a recording can be aimed at one app.

    `wmctrl` lists EWMH windows; geometry is in root coordinates. On Wayland this only sees
    XWayland clients, which is why the region is confirmed by the caller rather than assumed.
    """
    import shutil

    if not shutil.which("wmctrl"):
        return []
    out = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        hexid, desk, x, y, w, h, host, title = parts
        found.append({"id": hexid, "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                      "title": title.strip(), "geometry": f"{w}x{h}+{x}+{y}"})
    return found


def desktop(out: Path, seconds: float = 10.0, region: str | None = None,
            fps: int = 30) -> dict:
    """Record the desktop itself — for anything that is not a web page.

    On Wayland this needs `wf-recorder` (it speaks the compositor's screencopy protocol directly,
    so no portal and no root are involved once installed). It is not installed on this host, and
    `sudo` needs a password here, so this function fails with the exact command instead of
    pretending it can capture. OBS is the manual fallback: record, then pass the file through
    `POST /films/{id}/beats/{bid}/footage`.
    """
    import shutil

    rec = shutil.which("wf-recorder")
    if not rec:
        return {"ok": False, "error": "wf-recorder not installed",
                "install": "sudo zypper install wf-recorder",
                "alternative": "record with OBS, then use POST /films/{id}/beats/{bid}/footage"}
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [rec, "-f", str(out), "--codec", "libx264", "--framerate", str(fps)]
    if region:
        cmd += ["--geometry", region]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.send_signal(2)  # SIGINT: wf-recorder finalises the file on this
        proc.wait(timeout=20)
    if not out.is_file():
        return {"ok": False, "error": "capture produced no file",
                "stderr": (proc.stderr.read() if proc.stderr else "")[-300:]}
    return {"ok": True, "output": str(out), "seconds": _seconds(out), "fps": fps}
