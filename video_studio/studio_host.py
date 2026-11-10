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
    return {"ok": True, "plugin": "hewn-video-studio", "workspace": str(MOTION)}


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
