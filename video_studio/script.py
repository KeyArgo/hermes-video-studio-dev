"""The script room: an idea becomes beats with spoken lines, before any HTML exists.

Everything upstream of this file was hand-written: a person wrote each shot's HTML and typed
the voice-over. That is why films started from the middle. This module starts at the top —
an idea and a target length — and produces a treatment the storyboard can consume:

    idea ──> beats (spoken line + visual + shot prompt + seconds) ──> films/<id>.json

Two rules keep it honest:
  * the durations must sum to the target; a treatment that does not fit is rejected, not
    silently stretched by the renderer;
  * a line is written to be spoken, so it is checked for length, not for beauty. Taste is
    still a human call, and the treatment is meant to be edited.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

LANE_DEFAULT = "http://10.0.0.50:8890/v1/chat/completions"
LANE_MODEL_DEFAULT = "DeepSeek-V4.1-Flash-1017"

MAX_WORDS_PER_LINE = 22
TOLERANCE = 0.06          # a treatment may miss the target by 6 % before it is refused


class ScriptError(RuntimeError):
    pass


@dataclass
class Beat:
    id: str
    label: str
    line: str            # spoken voice-over
    seconds: float
    visual: str          # what is on screen, in plain words
    prompt: str = ""     # for an image/video model, if this beat is generated
    renderer: str = "html"   # "html" (HyperFrames) | "comfy" (H3/Wan) | "asset"

    def words(self) -> int:
        return len([w for w in re.split(r"\s+", self.line.strip()) if w])


@dataclass
class Treatment:
    title: str = "untitled"
    logline: str = ""
    total: float = 0.0
    accent: str = "#3b82f6"
    beats: list[Beat] = field(default_factory=list)

    def duration(self) -> float:
        return round(sum(b.seconds for b in self.beats), 3)

    def to_dict(self) -> dict:
        return {"title": self.title, "logline": self.logline, "total": self.total,
                "accent": self.accent, "beats": [asdict(b) for b in self.beats]}


# ------------------------------------------------------------------ the model call
def _lane() -> tuple[str, str, dict]:
    url = os.environ.get("STUDIO_LANE_URL") or LANE_DEFAULT
    model = os.environ.get("STUDIO_LANE_MODEL") or LANE_MODEL_DEFAULT
    extra = {"chat_template_kwargs": {"enable_thinking": False}} if "10.0.0.50" in url else {}
    return url, model, extra


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise ScriptError(f"lane answered {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ScriptError(f"no lane at {url}: {exc}") from None


def brief(idea: str, seconds: float, shots: int) -> str:
    return (
        "You are writing a short product film. Reply with JSON only, no prose, no code fence.\n"
        f"Idea: {idea}\n"
        f"Target length: {seconds:.1f} seconds across exactly {shots} beats.\n"
        "Rules:\n"
        f"- Each beat: id (kebab-case, <=28 chars), label (2-4 words), line (spoken voice-over, "
        f"<= {MAX_WORDS_PER_LINE} words, no stage directions), seconds (number), visual (one sentence "
        "describing what is on screen), prompt (one sentence for an image/video model, only if the "
        "beat is generated rather than typed).\n"
        f"- The seconds must add up to {seconds:.1f} (+/- 1.5). Vary them: no beat under 3 seconds.\n"
        "- First beat hooks a stranger in the first two seconds: a claim or a number, never a logo.\n"
        "- Last beat says where to find it.\n"
        "- Do not invent metrics, customers, prices or awards. If a number is not given, describe "
        "the thing instead of quantifying it.\n"
        "Shape: {\"title\": str, \"logline\": str, \"beats\": [...]}"
    )


def draft(idea: str, seconds: float = 45.0, shots: int = 5, accent: str = "#3b82f6",
          timeout: float = 180.0) -> Treatment:
    """Ask the lane for a treatment and refuse it if it does not fit the target."""
    if not idea.strip():
        raise ScriptError("an idea is required")
    if seconds < 10 or seconds > 300:
        raise ScriptError("target length must be between 10 and 300 seconds")
    if shots < 2 or shots > 24:
        raise ScriptError("shots must be between 2 and 24")

    url, model, extra = _lane()
    body = {"model": model, "temperature": 0.7, "max_tokens": 2000,
            "messages": [{"role": "user", "content": brief(idea, seconds, shots)}], **extra}
    answer = _post(url, body, timeout)
    try:
        text = answer["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ScriptError(f"unexpected lane response: {json.dumps(answer)[:300]}") from None

    return parse(text, target=seconds, accent=accent, shots=shots)


def parse(text: str, target: float, accent: str = "#3b82f6", shots: int | None = None) -> Treatment:
    """Turn the model's JSON into a validated treatment. Every rejection names its reason."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.M).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ScriptError("the lane did not return JSON")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ScriptError(f"the lane returned invalid JSON: {exc}") from None

    raw = data.get("beats") or []
    if shots and len(raw) != shots:
        raise ScriptError(f"expected {shots} beats, got {len(raw)}")

    beats, seen = [], set()
    for i, item in enumerate(raw, 1):
        bid = _slug(str(item.get("id") or f"beat-{i:02d}"))
        if bid in seen:
            bid = f"{bid}-{i}"
        seen.add(bid)
        try:
            seconds = float(item.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds < 2.0:
            raise ScriptError(f"beat {bid} is {seconds}s — under the 2 s minimum")
        beat = Beat(id=bid, label=str(item.get("label") or bid)[:40],
                    line=str(item.get("line") or "").strip(), seconds=round(seconds, 2),
                    visual=str(item.get("visual") or "").strip(),
                    prompt=str(item.get("prompt") or "").strip(),
                    renderer=str(item.get("renderer") or "html"))
        if not beat.line:
            raise ScriptError(f"beat {bid} has no spoken line")
        if beat.words() > MAX_WORDS_PER_LINE:
            raise ScriptError(f"beat {bid} speaks {beat.words()} words — too long to read aloud")
        beats.append(beat)

    treatment = Treatment(title=str(data.get("title") or "untitled")[:80],
                          logline=str(data.get("logline") or "")[:300],
                          total=float(target), accent=accent, beats=beats)
    total = treatment.duration()
    if abs(total - target) > max(1.5, target * TOLERANCE):
        raise ScriptError(f"beats add up to {total:.1f}s, target is {target:.1f}s")
    return treatment


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:28] or "beat"


# ------------------------------------------------------------------- to the storyboard
def to_film(treatment: Treatment, film_id: str | None = None) -> dict:
    """The film file the storyboard and renderer already understand, plus per-beat lines."""
    fid = _slug(film_id or treatment.title)
    beats = []
    for beat in treatment.beats:
        beats.append({"id": f"shot-{beat.id}", "duration": beat.seconds,
                      "src": f"compositions/shot-{beat.id}.html",
                      "label": beat.label, "line": beat.line, "visual": beat.visual,
                      "prompt": beat.prompt, "renderer": beat.renderer})
    return {"id": fid, "title": treatment.title, "accent": treatment.accent,
            "logline": treatment.logline, "beats": beats,
            "total": round(sum(b["duration"] for b in beats), 3)}


def save(treatment: Treatment, workspace: Path, film_id: str | None = None) -> dict:
    """Write films/<id>.json and films/<id>.script.json next to it (the treatment, unedited)."""
    film = to_film(treatment, film_id)
    films = Path(workspace) / "films"
    films.mkdir(parents=True, exist_ok=True)
    film_path = films / f"{film['id']}.json"
    if film_path.exists():
        raise ScriptError(f"{film_path.name} already exists — pick another title or delete it")
    film_path.write_text(json.dumps(film, indent=2))
    (films / f"{film['id']}.script.json").write_text(json.dumps(treatment.to_dict(), indent=2))
    return {"film": str(film_path), **film}
