"""Routes for the script room: idea → treatment → film file."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .script import Treatment, ScriptError, draft, parse, save

router = APIRouter()


def _workspace() -> Path:
    from .films_api import MOTION
    return MOTION


@router.post("/script/draft")
def post_draft(body: dict):
    """Draft a treatment. Nothing is written until /script/save."""
    try:
        treatment = draft(str(body.get("idea") or ""),
                          float(body.get("seconds") or 45),
                          int(body.get("shots") or 5),
                          str(body.get("accent") or "#3b82f6"),
                          float(body.get("timeout") or 180))
    except ScriptError as exc:
        raise HTTPException(422, str(exc)) from None
    return treatment.to_dict()


@router.post("/script/check")
def post_check(body: dict):
    """Validate a treatment the user edited by hand (durations, lines, beats)."""
    beats = body.get("beats") or []
    if not isinstance(beats, list) or not beats:
        raise HTTPException(422, "beats is required")
    payload = {"title": body.get("title") or "untitled", "logline": body.get("logline") or "",
               "beats": beats}
    try:
        treatment = parse(__import__("json").dumps(payload),
                          float(body.get("seconds") or sum(float(b.get("seconds") or 0) for b in beats)),
                          str(body.get("accent") or "#3b82f6"),
                          int(body["shots"]) if body.get("shots") else None)
    except ScriptError as exc:
        raise HTTPException(422, str(exc)) from None
    return treatment.to_dict()


@router.post("/script/save")
def post_save(body: dict):
    """Write the film file (and its .script.json sidecar) into the workspace."""
    payload = {"title": body.get("title") or "untitled", "logline": body.get("logline") or "",
               "beats": body.get("beats") or []}
    try:
        treatment = parse(__import__("json").dumps(payload),
                          float(body.get("seconds") or sum(float(b.get("seconds") or 0) for b in payload["beats"])),
                          str(body.get("accent") or "#3b82f6"),
                          int(body["shots"]) if body.get("shots") else None)
        return save(treatment, _workspace(), body.get("id"))
    except ScriptError as exc:
        raise HTTPException(422, str(exc)) from None
