"""One beat, rendered by a generative model instead of typed HTML.

The film model already knows what each beat is about (`shot` prompt + `duration`); this turns
that into a ComfyUI job, then normalises the result so it can sit in the same cut as a
HyperFrames shot: 1920x1080, 30 fps, exactly the beat's duration.

Honest constraints, measured on the GB10 with MiniMax H3 (fp8):
  * 768x448, 124 frames, 20 steps  ≈ 192 s  → about 1.5 s of film per second of render;
  * the model's native frame rate is 24, so 30 fps output is a resample, not more information;
  * 768x448 upscaled to 1080p is soft. If the frame matters, render at 1344x768 and pay 2-3x.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

H3_FPS = 24

# Generated frames arrive with their own palette — H3 likes rainbow gradients. A film is one
# object, so a generated clip is graded toward the brand before it is allowed into the cut:
# desaturate most of the way, push toward blue, keep a little contrast. Cheap, and it is the
# difference between "an experiment sitting in the film" and "part of the film".
BRAND_GRADE = ("hue=s=0.28,eq=contrast=1.08:brightness=-0.01,"
               "colorchannelmixer=rr=0.42:gg=0.62:bb=1.10,"
               # a little grain: generated frames are perfectly clean, and next to a
               # rendered shot they read as a separate, plastic layer
               "noise=alls=5:allf=t")


def apply_grade(src: Path, dest: Path) -> dict:
    """Grade an already-normalised clip in place-ish (video only, audio dropped)."""
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", BRAND_GRADE,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                        "-pix_fmt", "yuv420p", "-an", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"grade failed: {r.stderr[-300:]}")
    return {"graded": str(dest)}
OUT_FPS = 30
OUT_W, OUT_H = 1920, 1080


def beat_prompt(beat: dict) -> str:
    """The prompt a generative model should see for this beat.

    A beat carries a director's note (`shot`) and the words on screen (`line`). The note is
    what the video should depict; the line is text, which models cannot spell reliably, so it
    is deliberately not sent as lettering.
    """
    note = (beat.get("shot") or "").strip()
    idea = (beat.get("line") or "").strip()
    if note and idea:
        return f"{note}. Mood: {idea}. Cinematic, shallow depth of field, no text, no lettering."
    return note or idea or "cinematic abstract motion, no text"


def frames_for(seconds: float, fps: int = H3_FPS) -> int:
    """H3 wants a frame count; 4k+1 keeps the sampler happy on most video models."""
    n = max(17, int(round(seconds * fps)))
    return n + ((4 - (n % 4)) % 4) + 1 if n % 4 != 1 else n


def normalise(src: Path, dest: Path, seconds: float, fps: int = OUT_FPS,
              grade: bool = True) -> dict:
    """Fit the clip to the cut: 1080p, constant fps, exact duration, no audio.

    A generated clip has its own audio track. In this pipeline the bed owns sound, so the
    track is dropped rather than mixed — two beds fighting each other is the usual result.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
          f"crop={OUT_W}:{OUT_H},fps={fps},setsar=1")
    if grade:
        vf += "," + BRAND_GRADE
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-t", f"{seconds:.3f}", "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17",
           "-pix_fmt", "yuv420p", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg normalise failed: {r.stderr[-400:]}")
    return {"output": str(dest), "seconds": seconds, "width": OUT_W, "height": OUT_H, "fps": fps}


def render_beat(comfy, graph: dict, beat: dict, dest: Path, workdir: Path,
                width: int = 768, height: int = 448, steps: int = 20,
                timeout: float = 1800.0) -> dict:
    """Submit one beat, wait, download, normalise. Returns timing and the output path.

    `graph` is the API-format workflow; parameters are addressed by node id because a graph
    saved by hand carries no `_meta.title` for the substitution helper to match on.
    """
    seconds = float(beat.get("duration") or beat.get("seconds") or 0)
    if seconds <= 0:
        raise ValueError("beat has no duration")
    params = {
        "6": {"prompt": beat_prompt(beat), "width": int(width), "height": int(height),
              "length": frames_for(seconds)},
        "9": {"steps": int(steps)},
        "15": {"filename_prefix": f"h3_{beat.get('id', 'beat')}"},
    }
    raw = Path(workdir) / f"h3-{beat.get('id', 'beat')}-raw.mp4"
    result = comfy.render(graph, params, raw, timeout=timeout)
    norm = normalise(raw, dest, seconds)
    try:
        raw.unlink()
    except OSError:
        pass
    return {**norm, "prompt": beat_prompt(beat), "comfy": result.get("seconds_spent"),
            "frames": frames_for(seconds), "source": "comfy"}


def load_graph(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    graph = data.get("prompt") if isinstance(data, dict) and "prompt" in data else data
    if not isinstance(graph, dict) or not graph:
        raise ValueError(f"{path} is not an API-format workflow")
    return graph


def which_ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"
