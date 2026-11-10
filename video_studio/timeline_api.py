"""Routes for the timeline: state, operations, undo, render."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import ops as timeline_ops

router = APIRouter()


def _workspace() -> Path:
    from .films_api import MOTION
    return MOTION


@router.get("/timeline/{film_id}")
def get_timeline(film_id: str):
    return timeline_ops.load(_workspace(), film_id)


@router.post("/timeline/{film_id}/ops")
def post_ops(film_id: str, body: dict):
    """Apply one op or a list of them; each successful call is undoable as a unit."""
    workspace = _workspace()
    state = timeline_ops.load(workspace, film_id)
    raw = (body.get("ops") if isinstance(body.get("ops"), list) else [body]) or []
    try:
        before = timeline_ops._clone(state)
        for op in raw:
            state = timeline_ops.apply_op(state, op)
    except timeline_ops.OpsError as exc:
        raise HTTPException(422, str(exc)) from None
    timeline_ops.push_history(workspace, film_id, before)
    return timeline_ops.save(workspace, state)


@router.post("/timeline/{film_id}/undo")
def post_undo(film_id: str):
    workspace = _workspace()
    try:
        state = timeline_ops.undo(workspace, film_id)
    except timeline_ops.OpsError as exc:
        raise HTTPException(409, str(exc)) from None
    return timeline_ops.save(workspace, state)


@router.post("/timeline/{film_id}/reset")
def post_reset(film_id: str):
    workspace = _workspace()
    state = timeline_ops.empty(film_id)
    return timeline_ops.save(workspace, state)


@router.post("/timeline/{film_id}/render")
def post_render(film_id: str, body: dict | None = None):
    """Render the timeline through MLT. Slow by nature — the caller polls nothing; it waits."""
    body = body or {}
    workspace = _workspace()
    state = timeline_ops.load(workspace, film_id)
    if not any(state["tracks"].get(t) for t in ("V1", "V2")):
        raise HTTPException(422, "the timeline has no picture yet")
    from .mlt_project import render

    out = Path(body.get("out") or workspace / "renders" / f"{film_id}-timeline.mp4")
    if not out.is_absolute():
        out = workspace / out
    project = timeline_ops.to_project(state, title=film_id)
    try:
        result = render(project, out, quality=str(body.get("quality") or "delivery"))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    return {**result, "clips": len(project.clips), "total": project.total}


@router.get("/timeline/{film_id}/video")
def get_video(film_id: str):
    from fastapi.responses import FileResponse
    path = _workspace() / "renders" / f"{film_id}-timeline.mp4"
    if not path.is_file():
        raise HTTPException(404, "no timeline render yet")
    return FileResponse(path, media_type="video/mp4")
