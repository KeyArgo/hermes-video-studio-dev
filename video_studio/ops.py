"""Timeline operations: the editing model behind the cut.

A timeline is a dict of tracks holding clips. Every change is an *operation* applied to a
plain-JSON state, and every operation pushes the previous state onto a history stack — so undo
is exact, not approximate, and a saved project can be replayed operation by operation.

Deliberate choices:
  * state is JSON, never an object graph, so it can be written to disk and diffed;
  * clips carry an ``in`` (source offset) separate from ``start`` (timeline position), which is
    what makes trimming different from moving;
  * rendering converts the state into an MLT project — the timeline does not know how to
    encode video, and the encoder does not know about tracks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

TRACKS = ("V1", "V2", "A1", "A2")
MAX_HISTORY = 60


class OpsError(ValueError):
    pass


# ------------------------------------------------------------------- state
def empty(film_id: str) -> dict:
    return {"film": film_id, "tracks": {t: [] for t in TRACKS}, "total": 0.0,
            "updated": time.time()}


def _clone(state: dict) -> dict:
    return json.loads(json.dumps(state))


def normalize(state: dict) -> dict:
    """Sort clips, drop overlaps the way an editor would, and recompute the total."""
    state = _clone(state)
    end = 0.0
    for name in TRACKS:
        clips = sorted(state["tracks"].get(name) or [], key=lambda c: float(c["start"]))
        clean, cursor = [], 0.0
        for clip in clips:
            start = float(clip["start"])
            if start < cursor - 1e-6:      # a clip cannot start inside the previous one
                start = cursor
            clip["start"] = round(start, 3)
            clip["duration"] = round(float(clip["duration"]), 3)
            clean.append(clip)
            cursor = start + clip["duration"]
            if name.startswith("V"):
                end = max(end, cursor)
        state["tracks"][name] = clean
    state["total"] = round(end, 3)
    state["updated"] = time.time()
    return state


def _track(state: dict, name: str) -> list:
    if name not in TRACKS:
        raise OpsError(f"unknown track {name}; tracks are {', '.join(TRACKS)}")
    return state["tracks"][name]


def _find(state: dict, clip_id: str) -> tuple[str, int]:
    for name in TRACKS:
        for i, clip in enumerate(state["tracks"][name]):
            if clip["id"] == clip_id:
                return name, i
    raise OpsError(f"no clip {clip_id}")


# -------------------------------------------------------------------- ops
def place(state: dict, asset: str, track: str = "V1", start: float = 0.0,
          duration: float = 0.0, clip_id: str | None = None, source_in: float = 0.0,
          gain: float = 0.0, fade: float = 0.0) -> dict:
    """Put an asset on a track: the first operation of an edit."""
    clips = _track(state, track)
    if not asset:
        raise OpsError("place needs an asset path")
    duration = float(duration)
    if duration <= 0:
        raise OpsError("place needs a positive duration")
    start = float(start)
    if start < 0:
        raise OpsError("start cannot be negative")
    new_id = clip_id or f"{Path(asset).stem}-{int(time.time() * 1000) % 100000}"
    if any(c["id"] == new_id for t in TRACKS for c in state["tracks"][t]):
        raise OpsError(f"clip id {new_id} already used")
    clips.append({"id": new_id, "asset": str(asset), "start": start,
                  "in": float(source_in), "duration": duration,
                  "gain": float(gain), "fade": float(fade)})
    return normalize(state)


def move(state: dict, clip_id: str, start: float) -> dict:
    track, i = _find(state, clip_id)
    if float(start) < 0:
        raise OpsError("start cannot be negative")
    state["tracks"][track][i]["start"] = float(start)
    return normalize(state)


def trim(state: dict, clip_id: str, in_point: float | None = None,
         duration: float | None = None) -> dict:
    """Change which part of the source is used, and how long the clip plays."""
    track, i = _find(state, clip_id)
    clip = state["tracks"][track][i]
    if in_point is not None:
        new_in = float(in_point)
        if new_in < 0:
            raise OpsError("in point cannot be negative")
        clip["in"] = new_in
    if duration is not None:
        new_dur = float(duration)
        if new_dur <= 0:
            raise OpsError("duration must be positive")
        clip["duration"] = new_dur
    return normalize(state)


def split(state: dict, clip_id: str, at: float) -> dict:
    """Cut a clip at a timeline position into two clips that keep their source offsets."""
    track, i = _find(state, clip_id)
    clip = state["tracks"][track][i]
    start, dur = float(clip["start"]), float(clip["duration"])
    offset = float(at) - start
    if offset <= 0.05 or offset >= dur - 0.05:
        raise OpsError(f"split point {at}s is outside the clip ({start}–{start + dur}s)")
    left = dict(clip, duration=round(offset, 3))
    right = dict(clip)
    right.update({"id": f"{clip['id']}-b", "start": round(start + offset, 3),
                  "in": round(float(clip["in"]) + offset, 3),
                  "duration": round(dur - offset, 3)})
    state["tracks"][track][i:i + 1] = [left, right]
    return normalize(state)


def remove(state: dict, clip_id: str, ripple: bool = False) -> dict:
    """Delete a clip. Ripple closes the gap, which is what "delete and close" means."""
    track, i = _find(state, clip_id)
    clip = state["tracks"][track].pop(i)
    if ripple:
        cut_end = float(clip["start"]) + float(clip["duration"])
        for other in state["tracks"][track][i:]:
            if float(other["start"]) >= cut_end - 1e-6:
                other["start"] = round(float(other["start"]) - float(clip["duration"]), 3)
    return normalize(state)


def gain(state: dict, clip_id: str, db: float) -> dict:
    track, i = _find(state, clip_id)
    state["tracks"][track][i]["gain"] = float(db)
    return normalize(state)


def fade(state: dict, clip_id: str, seconds: float) -> dict:
    track, i = _find(state, clip_id)
    clip = state["tracks"][track][i]
    if float(seconds) < 0 or float(seconds) > float(clip["duration"]) / 2:
        raise OpsError("fade must be between 0 and half the clip")
    clip["fade"] = float(seconds)
    return normalize(state)


def _pure(fn):
    """Every operation returns a new state; a caller's dict is never mutated in place."""
    def inner(state: dict, *args, **kwargs):
        return fn(_clone(state), *args, **kwargs)
    inner.__name__ = fn.__name__
    return inner


place, move, trim, split, remove, gain, fade = (
    _pure(place), _pure(move), _pure(trim), _pure(split), _pure(remove), _pure(gain), _pure(fade))

OPS = {"place": place, "move": move, "trim": trim, "split": split, "remove": remove,
       "gain": gain, "fade": fade}


def apply_op(state: dict, op: dict) -> dict:
    name = op.get("op")
    if name not in OPS:
        raise OpsError(f"unknown op {name}; known: {', '.join(sorted(OPS))}")
    args = {k: v for k, v in op.items() if k != "op"}
    if "in" in args:
        args["source_in"] = args.pop("in")
    return OPS[name](state, **args)


def apply_all(state: dict, ops: list[dict]) -> dict:
    for op in ops:
        state = apply_op(state, op)
    return state


# ------------------------------------------------------------- persistence
def load(workspace: Path, film_id: str) -> dict:
    path = Path(workspace) / "films" / f"{film_id}.timeline.json"
    if not path.is_file():
        return empty(film_id)
    return json.loads(path.read_text())


def save(workspace: Path, state: dict) -> dict:
    path = Path(workspace) / "films" / f"{state['film']}.timeline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))
    return {"path": str(path), **state}


def push_history(workspace: Path, film_id: str, state: dict) -> None:
    path = Path(workspace) / "films" / f"{film_id}.history.json"
    stack = json.loads(path.read_text()) if path.is_file() else []
    stack.append(state)
    path.write_text(json.dumps(stack[-MAX_HISTORY:]))


def undo(workspace: Path, film_id: str) -> dict:
    path = Path(workspace) / "films" / f"{film_id}.history.json"
    if not path.is_file():
        raise OpsError("nothing to undo")
    stack = json.loads(path.read_text())
    if not stack:
        raise OpsError("nothing to undo")
    previous = stack.pop()
    path.write_text(json.dumps(stack))
    return previous


# ------------------------------------------------------------------ render
def to_project(state: dict, title: str = "timeline"):
    """Convert the timeline into the MLT render model (video tracks first, then audio)."""
    from .mlt_project import Clip, Project

    project = Project(title=title, total=float(state.get("total") or 0))
    for name in ("V1", "V2"):
        for clip in state["tracks"].get(name) or []:
            project.clips.append(Clip(resource=clip["asset"], start=float(clip["start"]),
                                      duration=float(clip["duration"]),
                                      source_in=float(clip.get("in") or 0),
                                      kind="video", label=clip["id"]))
    for name in ("A1", "A2"):
        for clip in state["tracks"].get(name) or []:
            project.clips.append(Clip(resource=clip["asset"], start=float(clip["start"]),
                                      duration=float(clip["duration"]),
                                      source_in=float(clip.get("in") or 0),
                                      kind="audio", label=clip["id"]))
    return project
