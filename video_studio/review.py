"""Ask any model what should change in a film — with the evidence it needs to be specific.

The weakness of "look at my video and improve it" is context: a model given one frame and a vague
note answers with adjectives. So this module hands the model what a human editor would be holding:
the moment (measured), the shot's own source, the film's motion profile, and the note the user
typed while watching.

Two kinds of evidence, in priority order:
  * **measurements** — always available, cheap, and impossible to hand-wave: per-second motion,
    frame luma, contrast, colourfulness, edge density, plus the gate's verdict;
  * **the frame itself** — attached as an image when the chosen model accepts images. If the
    endpoint rejects the image, the call is retried text-only and the answer says so. A model that
    cannot see must not be described as if it could.

Any OpenAI-compatible endpoint works: the local lanes (DeepSeek, GLM, Ollama), LM Studio, the
Nous portal, OpenRouter. Nothing here hard-codes one vendor.
"""
from __future__ import annotations

import base64
import json
import math
import re
import subprocess
from pathlib import Path

DEFAULT_MODELS = [
    {"id": "deepseek-lane", "label": "DeepSeek V4.1 Flash (Spark A/B)", "model": "DeepSeek-V4.1-Flash-1017",
     "base_url": "http://10.0.0.50:8890/v1", "vision": False, "cost": "free (local)"},
    {"id": "ollama-43b", "label": "Hermes 43B (Spark E, q6)", "model": "hermes43-36b-q6k:latest",
     "base_url": "http://10.0.0.52:11434/v1", "vision": False, "cost": "free (local)"},
    {"id": "ollama-dsv4", "label": "DeepSeek V4 (Spark E, q2k)", "model": "dsv4-flash-q2k:latest",
     "base_url": "http://10.0.0.52:11434/v1", "vision": False, "cost": "free (local)"},
]

PROMPT = """You are a senior film editor reviewing one moment of a short product film.

WHAT YOU ARE LOOKING AT
- Film: {title} ({seconds:.1f}s, {beats} beats)
- Beat: {beat} — {beat_seconds:.1f}s at {beat_start:.1f}s. Its note to the designer was: {shot_note}
- The user stopped at {at:.2f}s and wrote: "{note}"

MEASURED FACTS AT THAT MOMENT
- motion in the surrounding seconds: {motion}
- this frame: luma {luma:.1f}/255, contrast {contrast:.1f}, colourfulness {colour:.1f}, edge density {edges:.3f}
- gate verdict on this cut: {gate}

THE SHOT'S SOURCE (HyperFrames composition, edited as HTML + GSAP)
```
{source}
```

WHAT TO RETURN
Return ONLY a JSON object, no prose around it:
{{
  "diagnosis": "one sentence naming the actual problem, not a mood",
  "evidence": ["which measurement or element in the source supports it"],
  "changes": [
    {{"target": "css selector or gsap tween or timing", "change": "exact edit, concrete values",
      "why": "what it fixes", "priority": 1}}
  ],
  "keep": ["what already works at this moment and must not be touched"],
  "risk": "what this change could break"
}}

Rules: at most 4 changes, ordered by priority. If the moment is fine, say so and return an empty
changes list — do not invent work. Never suggest a change you cannot tie to the measurements or the
source shown above. If you were given no image, say which conclusion is limited by that and how.
"""


# ---------------------------------------------------------------- measurements
def _frames_gray(path: Path, width: int = 320, height: int = 180) -> list[bytes]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"fps=30,scale={width}:{height}", "-pix_fmt", "gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    n = width * height
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]


def _diff(a: bytes, b: bytes) -> float:
    if not a or not b:
        return 0.0
    return 100.0 * sum(1 for x, y in zip(a, b) if abs(x - y) > 12) / len(a)


def motion_profile(path: Path) -> list[float]:
    """Percent of pixels that change across each second — the same measure the gate uses."""
    fr = _frames_gray(path)
    if len(fr) < 60:
        return []
    return [round(max(_diff(fr[s * 30], fr[s * 30 + 29]), _diff(fr[s * 30 + 15], fr[s * 30 + 29])), 2)
            for s in range(len(fr) // 30)]


def frame_stats(path: Path, at: float) -> dict:
    """Numbers describing one frame: brightness, contrast, colourfulness, edge density."""
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                          "-frames:v", "1", "-vf", "scale=480:270", "-pix_fmt", "rgb24",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    if not png:
        return {}
    w, h = 480, 270
    px = [(png[i], png[i + 1], png[i + 2]) for i in range(0, len(png) - 2, 3)]
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in px]
    mean = sum(lum) / len(lum)
    var = sum((v - mean) ** 2 for v in lum) / len(lum)
    sat = [max(p) - min(p) for p in px]
    colourfulness = sum(sat) / len(sat)
    # edge density: share of horizontally adjacent pixels that differ noticeably
    edges = 0
    for y in range(h):
        row = lum[y * w:(y + 1) * w]
        for x in range(1, w):
            if abs(row[x] - row[x - 1]) > 18:
                edges += 1
    return {"luma": round(mean, 1), "contrast": round(math.sqrt(var), 1),
            "colour": round(colourfulness, 1), "edges": round(edges / (h * (w - 1)), 3)}


def grab_frame(path: Path, at: float, dest: Path) -> Path | None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", str(dest)],
                       capture_output=True, text=True)
    return dest if r.returncode == 0 and dest.is_file() else None


# --------------------------------------------------------------------- models
def load_models(workspace: Path | None = None) -> list[dict]:
    """Fleet defaults, overridable by `models.json` in the workspace."""
    models = list(DEFAULT_MODELS)
    if workspace:
        custom = Path(workspace) / "models.json"
        if custom.is_file():
            try:
                extra = json.loads(custom.read_text())
                if isinstance(extra, list):
                    models = extra + [m for m in models
                                      if m["id"] not in {e.get("id") for e in extra}]
            except json.JSONDecodeError:
                pass
    return models


def find_model(model_id: str, workspace: Path | None = None) -> dict:
    for m in load_models(workspace):
        if m.get("id") == model_id or m.get("model") == model_id:
            return m
    raise KeyError(f"unknown model {model_id!r}")


def probe(model: dict, timeout: float = 6.0) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen(model["base_url"].rstrip("/") + "/models", timeout=timeout) as r:
            data = json.loads(r.read())
        ids = [d.get("id") for d in data.get("data", [])]
        return {"id": model["id"], "up": True, "models": ids,
                "model_present": model["model"] in ids}
    except Exception as exc:  # noqa: BLE001
        return {"id": model["id"], "up": False, "error": str(exc)[:160]}


# ----------------------------------------------------------------------- ask
def ask(model: dict, prompt: str, image: Path | None = None,
        timeout: float = 300.0) -> dict:
    """One completion. Tries the image first when the model claims vision support."""
    import urllib.request

    url = model["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if model.get("api_key"):
        headers["Authorization"] = f"Bearer {model['api_key']}"

    def body(with_image: bool) -> dict:
        content = prompt
        if with_image and image and image.is_file():
            b64 = base64.b64encode(image.read_bytes()).decode()
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]
        return {"model": model["model"], "temperature": 0.3,
                "messages": [{"role": "user", "content": content}],
                "chat_template_kwargs": {"enable_thinking": False}}

    attempts = [bool(image and model.get("vision"))] if image else [False]
    last = ""
    for with_image in attempts:
        req = urllib.request.Request(url, data=json.dumps(body(with_image)).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {"ok": True, "text": text, "image_sent": with_image, "model": model["model"]}
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:300]
            if with_image:
                continue  # an endpoint that cannot take an image is not a failure of the review
    return {"ok": False, "error": last, "model": model["model"]}


def parse_json(text: str) -> dict | None:
    """Models wrap JSON in fences and prose; take the first object that parses."""
    text = text.strip()
    for candidate in re.findall(r"\{.*?\}(?=\s*$|\s*```)", text, re.S)[::-1] + [text]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    # last resort: brace matching from the first '{'
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
