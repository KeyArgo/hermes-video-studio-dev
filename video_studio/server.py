#!/usr/bin/env python3
"""Standalone dev server — the same UI and API the dashboard tab uses.

    python3 -m video_studio.server --port 8756      # then open http://127.0.0.1:8756/

Why it exists: the dashboard tab needs a running Hermes dashboard. This lets the
studio run on its own (and lets the tests drive the real HTTP surface) without
pretending to be part of Hermes.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.plugin_api import router  # noqa: E402

app = FastAPI(title="Video Studio Video Studio")
app.include_router(router, prefix="/api/plugins/video-studio")

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Video Studio Video Studio</title>
<style>html,body{margin:0;height:100%;background:#0b0d10;overflow:hidden}#root{position:absolute;inset:0}</style>
</head><body><div id="root"></div>
<script src="/index.js"></script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/index.js")
def bundle():
    return FileResponse(ROOT / "dashboard" / "dist" / "index.js",
                        media_type="application/javascript")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    import uvicorn
    print(f"Video Studio Video Studio → http://{a.host}:{a.port}/")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
