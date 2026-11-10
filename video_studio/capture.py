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
    regression in the interface shows up as a failed capture rather than a bad video.
    """
    steps = [
        {"wait": 900},
        {"click": ".room[data-room='films']", "after": 1200},
        {"wait": 900},
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
