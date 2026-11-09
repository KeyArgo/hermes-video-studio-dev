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


@app.get("/api/status")
def status():
    from video_studio.films_api import MOTION
    return {"ok": True, "plugin": "hewn-video-studio", "workspace": str(MOTION)}


@app.get("/", response_class=HTMLResponse)
def index():
    page = STUDIO / "index.html"
    if not page.is_file():
        return HTMLResponse("<h1>Video Studio</h1><p>studio/index.html is missing.</p>", status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/app.js")
def app_js():
    return FileResponse(str(STUDIO / "app.js"), media_type="application/javascript")


@app.get("/styles.css")
def styles():
    return FileResponse(str(STUDIO / "styles.css"), media_type="text/css")


if (STUDIO / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(STUDIO / "assets")), name="assets")


def main() -> None:
    import uvicorn

    host = os.environ.get("VIDEO_STUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("VIDEO_STUDIO_PORT", "8891"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
