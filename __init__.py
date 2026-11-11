"""Video Studio Video Studio — plugin glue.

One tool, `video_studio`, over the same service layer the dashboard tab calls.
That is deliberate: the agent and the person edit the *same* project file, so a
cut you made by clicking is the cut the agent sees, and vice versa.

Rules:
  * every action answers JSON, including on failure
  * the agent works in project ids and timeline seconds, never ffmpeg arguments
  * render and verify are separate so "it rendered" can never be read as
    "it is shippable"
"""
from __future__ import annotations

import json
import os
from typing import Any

try:  # loaded as a package
    from .video_studio import edl, service, store
except ImportError:  # loaded as a plain directory
    from video_studio import edl, service, store  # type: ignore[no-redef]

TOOLSET = "video"
DEFAULT_MAX = 90.0


def _dump(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _brief(v: dict) -> dict:
    """A view without the per-segment dump — the agent does not need 40 rows."""
    return {
        "project": v.get("id") or v.get("title"),
        "title": v.get("title"),
        "source": v.get("source"),
        "source_duration": v.get("source_duration"),
        "duration": v.get("duration"),
        "segments": len(v.get("segments") or []),
        "captions": len(v.get("captions") or []),
        "ops": v.get("op_count"),
        "renders": [r["name"] for r in (v.get("renders") or [])][:5],
    }


def tool_video_studio(args: dict, **kw) -> str:
    try:
        action = (args.get("action") or "projects").strip()
        pid = (args.get("project") or "").strip()

        if action == "projects":
            return _dump({"ok": True, "projects": store.list_projects()})

        if action == "open":
            return _dump({"ok": True, **service.project_view(pid)})

        if action == "new":
            src = os.path.expanduser((args.get("source") or "").strip())
            if not src:
                raise ValueError("source is required")
            v = service.create(src, args.get("title") or None,
                               args.get("entry") or "", args.get("accent") or "#3b82f6")
            return _dump({"ok": True, **_brief(v),
                          "note": "project id is what every other action takes"})

        if action == "analyze":
            a = service.analyze(pid)
            return _dump({"ok": True, **a,
                          "note": ("head/tail silence is the title and end cards — "
                                   "never proposed as a cut")})

        if action in ("snip", "tighten", "speed", "mute", "caption", "undo", "trim",
                      "card", "drop", "edit", "relink"):
            payload = {k: v for k, v in args.items()
                       if k not in ("action", "project") and v is not None}
            v = service.apply_op(pid, action, **payload)
            return _dump({"ok": True, **_brief(v)})

        if action == "render":
            r = service.render(pid, bool(args.get("fast")),
                               float(args.get("max_seconds") or DEFAULT_MAX))
            return _dump({"ok": True, **{k: r[k] for k in
                                         ("rendered", "kind", "duration", "shippable",
                                          "failures", "checks")},
                          "next": ("fix the failures and render again" if r["shippable"] is False
                                   else "shippable" if r["shippable"] else
                                   "preview only — a real render must pass the gate")})

        if action == "verify":
            path = os.path.expanduser((args.get("path") or "").strip())
            if not path:
                raise ValueError("path is required for verify")
            return _dump({"ok": True, **edl.Project(path).verify(
                path, max_seconds=float(args.get("max_seconds") or DEFAULT_MAX))})

        return _dump({"ok": False, "error": f"unknown action {action!r}"})
    except Exception as exc:  # noqa: BLE001 — a tool must always answer
        return _dump({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


SCHEMA_VIDEO_STUDIO = {
    "name": "video_studio",
    "description": (
        "Edit video in Video Studio Video Studio: a non-destructive timeline the person can "
        "also see and click. Workflow: projects/new/open -> analyze -> cut -> render.\n\n"
        "Times are ALWAYS seconds on the current timeline (after existing edits), "
        "not source time. Actions: projects (list) · open (project view) · new "
        "(source file -> project) · analyze (silences, scene changes, loudness, "
        "suggested_tighten, hook_is_static) · snip · tighten (drop internal pauses; "
        "title/end cards are protected) · trim · speed · mute · caption (7 words "
        "max) · undo · render (writes an mp4 and runs the gate; fast=true writes a "
        "rough preview that is NOT gated) · verify (gate an existing file).\n\n"
        "render returns shippable plus the failed checks. If shippable is false, fix "
        "and render again. Never describe a cut as good without a passing render."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["projects", "open", "new", "analyze", "snip", "tighten",
                         "trim", "speed", "mute", "caption", "card", "undo", "drop",
                         "edit", "relink", "render", "verify"],
                "description": "What to do.",
            },
            "project": {"type": "string", "description": "Project id (from projects/new)."},
            "source": {"type": "string", "description": "for new: path to the video file."},
            "title": {"type": "string", "description": "for new: display title."},
            "entry": {"type": "string", "description": "for new: which product this cut is for."},
            "accent": {"type": "string", "description": "for new: hex accent colour."},
            "start": {"type": "number", "description": "range start, seconds."},
            "end": {"type": "number", "description": "range end, seconds."},
            "factor": {"type": "number", "description": "for speed: 2.0 = twice as fast."},
            "text": {"type": "string", "description": "for caption: 7 words maximum."},
            "index": {"type": "integer",
                      "description": "for drop/edit: which op (0-based, as shown by open/timeline)."},
            "source": {"type": "string", "description": "for new/relink: path to the video file."},
            "install": {"type": "string",
                        "description": "for card kind=end: the install command to print."},
            "kind": {"type": "string", "enum": ["title", "limit", "end"],
                     "description": "for card: which brand card to render and insert."},
            "seconds": {"type": "number",
                        "description": "for card: how long the card stays on screen (default 1.2)."},
            "at": {"type": "number",
                   "description": "for card: timeline second to insert at; omit to append."},
            "fast": {"type": "boolean", "description": "for render: rough preview, not gated."},
            "max_seconds": {"type": "number", "description": "duration cap (default 90)."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

TOOLS = (("video_studio", SCHEMA_VIDEO_STUDIO, tool_video_studio),)


def register(ctx: Any) -> None:
    global DEFAULT_MAX
    try:
        DEFAULT_MAX = float(ctx.get_config("max_seconds", DEFAULT_MAX) or DEFAULT_MAX)
    except Exception:  # noqa: BLE001
        pass
    try:
        ff = str(ctx.get_config("ffmpeg", "") or "").strip()
        if ff:
            edl.FFMPEG = ff
            head = os.path.dirname(ff)
            edl.FFPROBE = os.path.join(head, "ffprobe") if head else "ffprobe"
    except Exception:  # noqa: BLE001
        pass

    for name, schema, handler in TOOLS:
        try:
            ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler)
        except TypeError:  # older cores
            ctx.register_tool(name=name, schema=schema, handler=handler)


__all__ = ["register"]
