"""Films backend — the HyperFrames side of Video Studio.

A *film* is `films/<id>.json` in the motion workspace (`~/Development/hewn-motion`
by default): an ordered list of beats, each pointing at one shot composition.
This router lets the tab list films, read and edit a shot's HTML, re-render one
shot, render the whole film, run the quality gate, and stream the result.

Nothing here invents a number: durations come from the film file, gate results
come from the gate script, and a failed render returns the log tail.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

router = APIRouter()

def _pick_workspace() -> Path:
    """Env override, else the user's motion project, else the bundled demo film."""
    env = os.environ.get("VIDEO_STUDIO_MOTION")
    if env:
        return Path(env).expanduser()
    real = Path.home() / "Development" / "hewn-motion"
    if (real / "films").is_dir():
        return real
    return Path(__file__).resolve().parent.parent / "demo" / "film"


MOTION = _pick_workspace()
FILMS = MOTION / "films"
FILM_DIR = MOTION / "film"
RENDERS = MOTION / "renders"
SHOTS_OUT = RENDERS / "shots"
HF = "hyperframes@0.9.26"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _motion() -> Path:
    if not (FILMS.is_dir() and FILM_DIR.is_dir()):
        raise HTTPException(status_code=503,
                            detail=f"motion workspace not found at {MOTION} (set VIDEO_STUDIO_MOTION)")
    return MOTION


def _clean(value: str, what: str = "name") -> str:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise HTTPException(status_code=400, detail=f"invalid {what}: {value!r}")
    return value


def _film_path(fid: str) -> Path:
    _motion()
    p = FILMS / f"{_clean(fid, 'film id')}.json"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"no film {fid}")
    return p


def _load(fid: str) -> dict:
    return json.loads(_film_path(fid).read_text())


def _shot(beat: dict) -> Path:
    src = beat.get("src") or f"compositions/{beat['id']}.html"
    p = (FILM_DIR / src).resolve()
    if FILM_DIR.resolve() not in p.parents:
        raise HTTPException(status_code=400, detail="shot outside the film directory")
    return p


def _rendered(fid: str) -> list[dict]:
    if not RENDERS.is_dir():
        return []
    out = []
    for p in sorted(RENDERS.glob(f"{fid}*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"name": p.name, "bytes": p.stat().st_size,
                    "mtime": int(p.stat().st_mtime), "final": p.name.endswith("-final.mp4")})
    return out


def _shot_render(beat_id: str) -> Path | None:
    p = SHOTS_OUT / f"{beat_id}.mp4"
    return p if p.is_file() else None


@router.get("/films")
def list_films():
    _motion()
    films = []
    for p in sorted(FILMS.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        beats = data.get("beats") or []
        films.append({
            "id": p.stem,
            "title": data.get("title") or p.stem,
            "total": data.get("total") or sum(float(b.get("duration", 0)) for b in beats),
            "beats": len(beats),
            "accent": data.get("accent"),
            "renders": _rendered(p.stem),
        })
    return {"films": films, "workspace": str(MOTION)}


@router.get("/films/{fid}")
def get_film(fid: str):
    data = _load(fid)
    beats = []
    for b in data.get("beats") or []:
        shot = _shot(b)
        out = _shot_render(b["id"])
        beats.append({
            "id": b["id"],
            "duration": float(b.get("duration", 0)),
            "src": b.get("src"),
            "exists": shot.is_file(),
            "bytes": shot.stat().st_size if shot.is_file() else 0,
            "shot_render": out.name if out else None,
            "vo": b.get("vo"),
            "text": b.get("text"),
        })
    return {"id": fid, "title": data.get("title") or fid,
            "total": data.get("total"), "beats": beats, "renders": _rendered(fid)}


@router.get("/films/{fid}/beats/{bid}/source")
def get_shot(fid: str, bid: str):
    data = _load(fid)
    beat = next((b for b in data.get("beats") or [] if b.get("id") == bid), None)
    if not beat:
        raise HTTPException(status_code=404, detail=f"no beat {bid} in {fid}")
    p = _shot(beat)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"shot file missing: {p.name}")
    try:
        rel = str(p.relative_to(FILM_DIR.resolve()))
    except ValueError:
        rel = p.name
    return {"id": bid, "path": rel, "source": p.read_text()}


@router.put("/films/{fid}/beats/{bid}/source")
def put_shot(fid: str, bid: str, body: dict):
    data = _load(fid)
    beat = next((b for b in data.get("beats") or [] if b.get("id") == bid), None)
    if not beat:
        raise HTTPException(status_code=404, detail=f"no beat {bid} in {fid}")
    src = body.get("source")
    if not isinstance(src, str) or len(src) > 200_000:
        raise HTTPException(status_code=400, detail="source must be a string under 200 KB")
    if "data-composition-id" not in src:
        raise HTTPException(status_code=400, detail="source must keep data-composition-id")
    p = _shot(beat)
    p.write_text(src if src.endswith("\n") else src + "\n")
    return {"saved": True, "bytes": p.stat().st_size}


def _run(cmd: list[str], timeout: int = 900) -> dict:
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(FILM_DIR), capture_output=True, text=True, timeout=timeout)
    return {"ok": proc.returncode == 0, "rc": proc.returncode,
            "seconds": round(time.time() - started, 1),
            "tail": (proc.stdout + "\n" + proc.stderr)[-2000:]}


def _rebuild_index() -> dict:
    return _run(["python3", str(MOTION / "tools" / "build_index.py"), "--out", "index.html"],
                120)


@router.post("/films/{fid}/build")
def build_index(fid: str):
    _film_path(fid)
    r = _run(["python3", str(MOTION / "tools" / "build_index.py"),
              str(FILMS / f"{fid}.json"), "--out", "index.html"], 180)
    if not r["ok"]:
        raise HTTPException(status_code=500, detail=r["tail"])
    return r


def _job(fid: str, kind: str, cmd: list[str]) -> str:
    jid = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[jid] = {"id": jid, "film": fid, "kind": kind, "state": "running",
                      "started": time.time(), "cmd": cmd, "tail": ""}

    def work():
        try:
            r = _run(cmd, 1800)
            with _lock:
                _jobs[jid].update({"state": "done" if r["ok"] else "failed",
                                   "ok": r["ok"], "rc": r["rc"],
                                   "seconds": r["seconds"], "tail": r["tail"]})
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _jobs[jid].update({"state": "failed", "ok": False, "tail": str(exc)})

    threading.Thread(target=work, daemon=True).start()
    return jid


@router.post("/films/{fid}/render")
def render_film(fid: str):
    _film_path(fid)
    b = _run(["python3", str(MOTION / "tools" / "build_index.py"),
              str(FILMS / f"{fid}.json"), "--out", "index.html"], 180)
    if not b["ok"]:
        raise HTTPException(status_code=500, detail=b["tail"])
    out = f"../renders/{fid}-raw.mp4"
    jid = _job(fid, "film", ["npx", "--yes", HF, "render", "--output", out])
    return {"job": jid, "output": out}


@router.post("/films/{fid}/beats/{bid}/render")
def render_shot(fid: str, bid: str):
    data = _load(fid)
    beat = next((b for b in data.get("beats") or [] if b.get("id") == bid), None)
    if not beat:
        raise HTTPException(status_code=404, detail=f"no beat {bid} in {fid}")
    src = beat.get("src") or f"compositions/{bid}.html"
    SHOTS_OUT.mkdir(parents=True, exist_ok=True)
    out = f"../renders/shots/{bid}.mp4"
    jid = _job(f"{fid}#{bid}", "shot",
               ["npx", "--yes", HF, "render", "--composition", src,
                "--quality", "draft", "--output", out])
    return {"job": jid, "output": out}


@router.get("/jobs/{jid}")
def job(jid: str):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            raise HTTPException(status_code=404, detail="no such job")
        return dict(j)


@router.post("/films/{fid}/gate")
def gate(fid: str):
    video = body_video = None
    body_video = None
    cands = [RENDERS / f"{fid}-final.mp4", RENDERS / f"{fid}-raw.mp4"]
    video = next((p for p in cands if p.is_file()), None)
    if video is None:
        raise HTTPException(status_code=404, detail=f"no render for {fid} yet")
    code = (
        "import json,sys;sys.path.insert(0,%r);import gate;"
        "f=sys.argv[1];print(json.dumps(gate.verdict([gate.check_hook(f),"
        "gate.check_motion(f),gate.check_legibility(%r)])))"
        % (str(MOTION / "tools"), str(FILM_DIR))
    )
    r = subprocess.run(["python3", "-c", code, str(video)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=(r.stdout + r.stderr)[-1500:])
    return {"video": video.name, **json.loads(r.stdout.strip().splitlines()[-1])}


@router.get("/films/{fid}/video")
def video(fid: str):
    _film_path(fid)
    cands = [RENDERS / f"{fid}-final.mp4", RENDERS / f"{fid}-raw.mp4"] + _render_glob(fid)
    for p in cands:
        if p and p.is_file():
            return FileResponse(str(p), media_type="video/mp4")
    raise HTTPException(status_code=404, detail=f"no render for {fid} yet")


def _render_glob(fid: str) -> list[Path]:
    if not RENDERS.is_dir():
        return []
    return sorted(RENDERS.glob(f"{fid}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)


@router.get("/films/{fid}/beats/{bid}/video")
def shot_video(fid: str, bid: str):
    _film_path(fid)
    p = _shot_render(_clean(bid, "beat id"))
    if not p:
        raise HTTPException(status_code=404, detail=f"shot {bid} not rendered yet")
    return FileResponse(str(p), media_type="video/mp4")


@router.get("/studio/host")
def studio_host():
    """Where the studio app lives. Starts the loopback host when it is not running."""
    try:
        from video_studio.host_launcher import ensure_host
        return ensure_host()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"studio host unavailable: {exc}") from None
