"""Video Studio host — the studio app, served on loopback.

The desktop pane frames this; the dashboard tab and the agent tool call the same routes.
One host per data directory, recorded in ``studio-host.json`` beside the plugin data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_studio.films_api import router as films_router  # noqa: E402

STUDIO = ROOT / "studio"

app = FastAPI(title="Video Studio", docs_url=None, redoc_url=None)
app.include_router(films_router, prefix="/api")

from video_studio.assets_api import router as assets_router  # noqa: E402
from video_studio.comfy_api import router as comfy_router  # noqa: E402
app.include_router(assets_router, prefix="/api")
app.include_router(comfy_router, prefix="/api")

from video_studio.script_api import router as script_router  # noqa: E402
from video_studio.timeline_api import router as timeline_router  # noqa: E402
app.include_router(script_router, prefix="/api")
app.include_router(timeline_router, prefix="/api")


@app.get("/api/status")
def status():
    from video_studio.films_api import MOTION
    return {"ok": True, "plugin": "video-studio", "workspace": str(MOTION)}


def _install_reaper() -> None:
    """A host that dies must not leave renderers behind."""
    import atexit
    import signal

    from video_studio import jobs

    def _bye(*_a):
        try:
            jobs.reap_all(wait=3.0)
        finally:
            pass

    atexit.register(_bye)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, lambda s, f, _p=prev: (_bye(), os._exit(0)))
        except Exception:
            pass


try:
    _install_reaper()
except Exception:
    pass

# A crashed or killed host leaves children behind. Coming back up is the moment to prove the
# studio holds nothing it does not own: reap what a previous run left, then tidy the registry.
try:
    from video_studio import jobs as _jobs
    _startup = _jobs.startup_cleanup()
except Exception:
    _startup = {"error": "startup cleanup unavailable"}


@app.post("/api/host/close")
def host_close():
    """Stop the studio deliberately: kill every render first, then exit.

    Closing the window is not the same as stopping the work. Renders run in their own process
    groups, so without this they keep burning cores with nothing on screen to say so.
    """
    from video_studio import jobs
    reaped = jobs.reap_all()
    _write_state_gone()
    import threading
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"ok": True, **reaped}


def _write_state_gone():
    try:
        from video_studio.host_launcher import state_path
        state_path().unlink(missing_ok=True)
    except Exception:
        pass


@app.get("/api/preflight")
def preflight():
    """What this install can do, and the exact command for anything missing."""
    from video_studio.preflight import check
    return check()


@app.get("/api/version")
def version():
    """The build behind this page. Same function the dashboard tab and CLI read."""
    from video_studio.version_stamp import as_dict
    return {"build": as_dict()}


# Everything else under studio/ is served as-is. A mount (not one route per file) means a new
# panel or script works the moment it is written, instead of 404ing until the host is edited.
app.mount("/", StaticFiles(directory=str(STUDIO), html=True), name="studio")


def main() -> None:
    import uvicorn

    host = os.environ.get("VIDEO_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("VIDEO_STUDIO_PORT", "8891"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
