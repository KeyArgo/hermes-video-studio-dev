"""Shot authoring: a beat becomes a HyperFrames composition.

This is the step that used to be hand-written HTML. The recipe is the one that produced the
only shots that passed review: a scoped template, a paused GSAP timeline, an opaque frame 0,
continuous motion, and type that is legible at 1080p. The lane writes; the checks here decide
whether what came back is usable, and a refusal is reported instead of rendered.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .script import LANE_DEFAULT, LANE_MODEL_DEFAULT

MIN_TYPE_PX = 34
MAX_CHARS = 16000


class ShotError(RuntimeError):
    pass


CONTRACT = """You write ONE HyperFrames shot composition: a self-contained HTML file.

Hard requirements:
- Whole file is wrapped in <template> ... </template>.
- Inside: <style> scoped under #root, then a <div id="root" data-composition-id="{shot_id}"
  data-width="1920" data-height="1080">, then a <script>.
- The script builds `const tl = gsap.timeline({{ paused: true }})` with at least 3 tweens and
  sets `window.__timelines = window.__timelines || {{}}; window.__timelines["{shot_id}"] = tl;`
- gsap is already loaded globally; do NOT import anything, no network, no external fonts, no images.
- Frame 0 must be fully legible and opaque: the headline visible, background painted, >= 80% opacity.
- EVERY tween must carry an explicit time position as its last argument (`, 0` for "start at
  zero"). A tween without a position is appended after the previous one, which silently shifts
  all motion past the end of the shot — this produced static seconds in practice.
- Motion must continue for the WHOLE {seconds}s: a slow persistent background move (a blurred
  orb, a grid drift, a gradient sweep) plus one entrance. Never leave a static stretch.
- Type: sizes >= 34px. No text may overflow its container. Max 12 words of headline.
- Palette: background {bg}, text #e8eaed, dim #8b929c, accent {accent}. No gradients in text,
  no white boxes, no drop shadows larger than 24px.
- Copy: the headline is the spoken line, tightened. No invented numbers, no lorem ipsum.

Subject for this shot: {visual}
Spoken line: {line}
On-screen label (optional, short): {label}
Duration: {seconds} seconds.
"""


def _lane() -> tuple[str, str, dict]:
    import os
    url = os.environ.get("STUDIO_LANE_URL") or LANE_DEFAULT
    model = os.environ.get("STUDIO_LANE_MODEL") or LANE_MODEL_DEFAULT
    extra = {"chat_template_kwargs": {"enable_thinking": False}} if "10.0.0.50" in url else {}
    return url, model, extra


def write_shot(beat: dict, accent: str = "#3b82f6", bg: str = "#0b0d10",
               out: Path | None = None, timeout: float = 240.0) -> dict:
    """Ask the lane for one composition and refuse it unless it satisfies the contract."""
    shot_id = str(beat.get("id") or "")
    if not shot_id:
        raise ShotError("beat has no id")
    seconds = float(beat.get("seconds") or beat.get("duration") or 0) or 6.0
    prompt = CONTRACT.format(shot_id=shot_id, seconds=seconds, bg=bg, accent=accent,
                             visual=(beat.get("visual") or "").strip() or beat.get("line") or "",
                             line=(beat.get("line") or "").strip(),
                             label=(beat.get("label") or "").strip())

    url, model, extra = _lane()
    body = {"model": model, "temperature": 0.6, "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}], **extra}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            answer = json.loads(resp.read() or b"{}")
        html = answer["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        raise ShotError(f"lane answered {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ShotError(f"no lane: {exc}") from None
    except (KeyError, IndexError, TypeError):
        raise ShotError("unexpected lane response") from None

    html = _strip_fence(html)
    problems = check(html, shot_id)
    if problems:
        raise ShotError(f"{shot_id}: " + "; ".join(problems))

    path = Path(out) if out else Path(f"compositions/shot-{shot_id}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html.rstrip() + "\n")
    return {"id": shot_id, "path": str(path), "bytes": len(html),
            "tweens": len(re.findall(r"\.(?:to|from|fromTo)\(", html))}


def _strip_fence(text: str) -> str:
    """Take the file out of whatever wrapper the model put around it.

    Models like to introduce the answer ("Here's a composition…") and fence it. Only the
    <template> element is the composition; anything around it makes the renderer fail, so it
    is cut here rather than discovered later by a broken render.
    """
    text = text.strip()
    start = text.find("<template>")
    end = text.rfind("</template>")
    if start >= 0 and end > start:
        text = text[start:end + len("</template>")]
    text = re.sub(r"^```(?:html)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def check(html: str, shot_id: str) -> list[str]:
    """Static checks for the things the renderer fails on, in the order they bite."""
    problems = []
    if len(html) > MAX_CHARS:
        problems.append(f"{len(html)} chars is too large")
    if not html.startswith("<template>"):
        problems.append("output does not start with <template>")
    if "<template>" not in html:
        problems.append("missing <template> wrapper")
    if f'data-composition-id="{shot_id}"' not in html:
        problems.append("missing data-composition-id")
    if 'data-width="1920"' not in html or 'data-height="1080"' not in html:
        problems.append("missing 1920x1080 dimensions")
    if "gsap.timeline" not in html:
        problems.append("no gsap timeline")
    if "paused" not in html:
        problems.append("timeline is not paused (it would autoplay and desync)")
    if "__timelines" not in html:
        problems.append("timeline not published on window.__timelines")
    if html.count("<script") > 1:
        problems.append("more than one script tag")
    if re.search(r"<(?:script|link)[^>]+(?:src|href)=[\"']https?:", html):
        problems.append("loads a remote asset")
    if re.search(r"@import|url\(\s*['\"]?https?:", html):
        problems.append("imports a remote stylesheet")
    return problems


def write_all(beats: list[dict], compositions: Path, accent: str = "#3b82f6",
              only_missing: bool = True) -> dict:
    """Author every beat that has no composition yet; report what was written and what failed."""
    written, skipped, failed = [], [], []
    for beat in beats:
        # a beat already carries the path the renderer will use; honour it instead of
        # inventing a second convention (which produced shot-shot-… files)
        src = beat.get("src") or f"compositions/shot-{beat['id']}.html"
        target = Path(compositions) / Path(src).name
        if only_missing and target.is_file():
            skipped.append(beat["id"])
            continue
        try:
            written.append(write_shot(beat, accent=accent, out=target))
        except ShotError as exc:
            failed.append({"id": beat["id"], "error": str(exc)})
    return {"written": written, "skipped": skipped, "failed": failed}
