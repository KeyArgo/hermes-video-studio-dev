"""Routes for the generation room: what the GPUs can do, and queueing a real render.

The host is not hard-coded: it comes from the film workspace (`comfy.json` next to the films)
so a different machine can be pointed at without editing code, and the default is the box
that actually holds the MiniMax H3 weights.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .comfy import KNOWN_MODELS, Comfy, ComfyError, load_graph

router = APIRouter()

CONFIG_NAME = "comfy.json"


def _workspace() -> Path:
    from .films_api import MOTION
    return MOTION


def load_config() -> dict:
    path = _workspace() / CONFIG_NAME
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def client() -> Comfy:
    cfg = load_config()
    return Comfy(cfg.get("host"))


@router.get("/comfy/status")
def comfy_status():
    """Reachability, GPU memory, queue depth, and which models are installed."""
    cfg = load_config()
    comfy = client()
    status = comfy.status()
    status["configured_host"] = cfg.get("host") or comfy.host
    status["workspace_config"] = str(_workspace() / CONFIG_NAME)
    return status


@router.get("/comfy/models")
def comfy_models():
    """The catalogue this plugin knows how to drive, regardless of what is installed."""
    return {"models": [{"id": k, **v} for k, v in KNOWN_MODELS.items()]}


@router.post("/comfy/render")
def comfy_render(body: dict):
    """Render one beat: a workflow file in the workspace, parameters by node title."""
    workflow = body.get("workflow")
    if not workflow:
        raise HTTPException(400, "workflow is required (a filename in films/workflows/)")
    graph_path = Path(workflow)
    if not graph_path.is_absolute():
        graph_path = _workspace() / "workflows" / workflow
    if not graph_path.is_file():
        raise HTTPException(404, f"no workflow at {graph_path}")
    out = body.get("out") or "renders/comfy/output.mp4"
    dest = Path(out)
    if not dest.is_absolute():
        dest = _workspace() / dest
    try:
        result = client().render(load_graph(graph_path), body.get("params") or {}, dest,
                                 timeout=float(body.get("timeout", 900)))
    except ComfyError as exc:
        raise HTTPException(503, str(exc)) from None
    return result


@router.post("/comfy/beat")
def comfy_beat(body: dict):
    """Render one film beat with MiniMax H3 and land it where the cut expects it.

    The clip is written to `renders/shots/<beat id>.mp4` — the same place a HyperFrames shot
    goes — so assembly does not care which engine made which beat.
    """
    from .gen_beat import load_graph, render_beat

    film_id = body.get("film_id")
    beat_id = body.get("beat_id")
    if not film_id or not beat_id:
        raise HTTPException(400, "film_id and beat_id are required")

    from .films_api import FILMS, RENDERS

    film_path = FILMS / f"{film_id}.json"
    if not film_path.is_file():
        raise HTTPException(404, f"no film {film_id}")
    data = json.loads(film_path.read_text())
    beat = next((b for b in data.get("beats") or [] if b.get("id") == beat_id), None)
    if beat is None:
        raise HTTPException(404, f"no beat {beat_id} in {film_id}")

    name = body.get("workflow") or "h3-minimal.api.json"
    graph_path = Path(name)
    if not graph_path.is_absolute():
        graph_path = _workspace() / "workflows" / name
    if not graph_path.is_file():
        raise HTTPException(404, f"no workflow at {graph_path}; copy it to {_workspace() / 'workflows'}")

    if body.get("prompt"):
        beat = {**beat, "shot": body["prompt"]}
    dest = RENDERS / "shots" / f"{beat_id}.mp4"
    try:
        result = render_beat(client(), load_graph(graph_path), beat, dest,
                             workdir=RENDERS / "h3",
                             width=int(body.get("width", 768)),
                             height=int(body.get("height", 448)),
                             steps=int(body.get("steps", 20)),
                             timeout=float(body.get("timeout", 1800)))
    except ComfyError as exc:
        raise HTTPException(503, str(exc)) from None
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc)) from None
    return {"film": film_id, "beat": beat_id, **result,
            "asset": str(dest.relative_to(_workspace()))}
