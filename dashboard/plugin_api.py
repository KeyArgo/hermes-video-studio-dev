"""Dashboard backend, mounted by Hermes under /api/plugins/hewn-video-studio.

Same shape as the other Hewn studios: a thin router over the service layer, so
the tab, the agent tool and the CLI all execute identical code.
"""
import os
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from video_studio import edl, service, store  # noqa: E402

router = APIRouter()

# Films: the HyperFrames side of the studio (films/<id>.json in the motion workspace).
import films as _films  # noqa: E402
router.include_router(_films.router)

# Assets: screenshots, screen recordings and other source material (proxy + thumbnail + sidecar).
from video_studio.assets_api import router as _assets_router  # noqa: E402
router.include_router(_assets_router)

# Generation: ComfyUI (MiniMax H3, Wan, music) as a renderer for beats.
from video_studio.comfy_api import router as _comfy_router  # noqa: E402
router.include_router(_comfy_router)

# Script room: idea -> treatment -> film file.
from video_studio.script_api import router as _script_router  # noqa: E402
router.include_router(_script_router)

# Timeline: place/trim/split/move/undo over a JSON edit, rendered by MLT.
from video_studio.timeline_api import router as _timeline_router  # noqa: E402
router.include_router(_timeline_router)

MAX_JSON_BODY_BYTES = 256 * 1024


def invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or 'not found') from None
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail='video operation failed') from None


async def json_body(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail='request body too large')
    if not raw:
        return {}
    import json as _json
    try:
        body = _json.loads(bytes(raw))
    except _json.JSONDecodeError:
        raise HTTPException(status_code=400, detail='invalid JSON') from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail='body must be an object')
    return body


@router.get('/projects')
def projects():
    return invoke(lambda: {'projects': store.list_projects()})


@router.post('/projects')
async def create_project(request: Request):
    body = await json_body(request)
    src = str(body.get('source') or '').strip()
    if not src:
        raise HTTPException(status_code=400, detail='source is required')
    return await run_in_threadpool(
        invoke, service.create, os.path.expanduser(src),
        body.get('title') or body.get('name') or None,
        body.get('entry') or '', body.get('accent') or '#3b82f6')


@router.get('/projects/{pid}')
def project(pid: str):
    return invoke(service.project_view, pid)


@router.post('/projects/{pid}/ops')
async def apply_op(pid: str, request: Request):
    body = await json_body(request)
    op = str(body.pop('op', '') or '')
    if not op:
        raise HTTPException(status_code=400, detail='op is required')
    return await run_in_threadpool(lambda: invoke(service.apply_op, pid, op, **body))


@router.post('/projects/{pid}/render')
async def render(pid: str, request: Request):
    body = await json_body(request)
    fast = bool(body.get('fast'))
    cap = float(body.get('max_seconds') or service.MAX_SECONDS)
    return await run_in_threadpool(invoke, service.render, pid, fast, cap)


@router.get('/projects/{pid}/analyze')
def analyze(pid: str):
    return invoke(service.analyze, pid)


@router.get('/projects/{pid}/poster.png')
def poster(pid: str):
    # First real frame. A <video> often paints nothing until playback starts, so
    # the studio always has a still to show instead of a black rectangle.
    p = store.load(pid)
    d = store.project_dir(pid)
    png = d / 'poster.png'
    if not png.exists() or png.stat().st_mtime < os.path.getmtime(p.source):
        edl._run(['ffmpeg', '-y', '-v', 'error', '-ss', '0.2', '-i', p.source,
                  '-frames:v', '1', '-vf', 'scale=1280:-2', str(png)])
    if not png.exists():
        raise HTTPException(status_code=404, detail='no frame available')
    return FileResponse(str(png), media_type='image/png')


@router.get('/projects/{pid}/waveform.png')
def waveform(pid: str, width: int = 1600, height: int = 160):
    path = invoke(service.waveform, pid, max(240, min(int(width), 4000)),
                  max(60, min(int(height), 400)))
    return FileResponse(path, media_type='image/png')


def _verify_source(pid: str, path: str) -> str:
    p = store.load(pid)
    if os.path.abspath(path) != os.path.abspath(p.source):
        raise HTTPException(status_code=403, detail='not this project\'s source')
    if not os.path.exists(p.source):
        raise HTTPException(status_code=404, detail='source file is gone')
    return p.source


@router.get('/projects/{pid}/source')
def source(pid: str):
    """Range-capable stream of the source, so the browser can scrub it."""
    path = invoke(_verify_source, pid, store.load(pid).source)
    return FileResponse(path, media_type='video/mp4',
                        headers={'Accept-Ranges': 'bytes'})


@router.get('/projects/{pid}/renders/{name}')
def rendered(pid: str, name: str):
    path = invoke(store.render_path, pid, name)
    return FileResponse(str(path), media_type='video/mp4',
                        headers={'Accept-Ranges': 'bytes'})


@router.get('/health')
def health():
    return JSONResponse({'ok': True,
                         'ffmpeg': edl._run(['ffmpeg', '-version']).returncode == 0,
                         'root': str(store.root())})


@router.get('/version')
def version():
    """The build this tab is actually running: BUILD_INFO.json -> git -> plugin.yaml.

    Read at request time from the same module the CLI prints, so the chip in the
    header and `python3 -m video_studio.cli version` can never disagree.
    """
    from video_studio.version_stamp import as_dict
    return JSONResponse({'build': as_dict()})
