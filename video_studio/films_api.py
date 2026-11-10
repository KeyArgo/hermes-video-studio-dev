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


def _save(fid: str, data: dict) -> None:
    """Write a film back. Atomic, so a crash cannot leave a half-written film behind."""
    path = _film_path(fid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


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
        # sidecars live beside films: .timeline.json, .history.json, .script.json are not films
        if p.name.endswith((".timeline.json", ".history.json", ".script.json")):
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(data, dict) or "beats" not in data:
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
        renderer = (b.get("renderer") or "html").lower()
        shot: Path | None = None
        try:
            shot = _shot(b)
        except HTTPException:
            shot = None  # a generative beat has no composition; that is not an error
        out = _shot_render(b["id"])
        beats.append({
            "id": b["id"],
            "duration": float(b.get("duration", 0)),
            "src": b.get("src"),
            "renderer": renderer,
            "exists": bool(shot and shot.is_file()),
            "bytes": shot.stat().st_size if shot and shot.is_file() else 0,
            "shot_render": out.name if out else None,
            "vo": b.get("vo"),
            "text": b.get("text"),
            "shot": b.get("shot"),
            "line": b.get("line"),
        })
    return {"id": fid, "title": data.get("title") or fid,
            "total": data.get("total"), "beats": beats, "renders": _rendered(fid),
            "treatment": data.get("treatment"), "music": data.get("music")}


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


@router.post("/films/{fid}/beats/{bid}/footage")
def beat_from_footage(fid: str, bid: str, body: dict | None = None):
    """Make a beat out of real captured footage instead of a composition.

    The clip is trimmed to the beat's duration, dressed to match the film (letterbox bars and a
    vignette, the same as the cinematic treatment uses), graded slightly cooler and given light
    grain so it sits beside rendered shots rather than looking pasted in. It is written to
    `renders/shots/<beat id>.mp4` like any other beat clip, so assembly is unchanged.
    """
    body = body or {}
    data = _load(fid)
    beat = next((b for b in data.get("beats") or [] if b.get("id") == bid), None)
    if beat is None:
        raise HTTPException(status_code=404, detail=f"no beat {bid} in {fid}")
    capture = body.get("capture") or "studio-walkthrough"
    if capture == "last":
        # the tight path: record, stop, then use what was just recorded without typing a name
        from .recorder import last_recording
        src = last_recording()
        if src is None:
            raise HTTPException(status_code=404, detail="no finished recording yet — POST /capture/record/stop first")
        capture = src.stem
    else:
        as_path = Path(str(capture)).expanduser()
        src = as_path if as_path.is_absolute() and as_path.is_file() \
            else RENDERS / "captures" / f"{_clean(capture, 'capture')}.mp4"
        capture = src.stem
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"no capture at {src}")

    seconds = float(body.get("seconds") or beat.get("duration") or 0)
    start = float(body.get("start") or 0.0)
    if seconds <= 0:
        raise HTTPException(status_code=400, detail="beat has no duration")

    # a screen recording is mostly still; the cut needs motion in every second, so the frame
    # is scaled 4% larger and slowly panned — the standard treatment for screen footage
    big_w, big_h = int(1920 * 1.04), int(1080 * 1.04)
    filt = [f"scale={big_w}:{big_h}:flags=lanczos", "fps=30", "setsar=1",
            f"crop=1920:1080:x='(iw-1920)*t/{seconds:.3f}':y='(ih-1080)*t/{seconds:.3f}'"]
    if body.get("letterbox", True):
        filt += ["drawbox=x=0:y=0:w=iw:h=ih*0.048:color=black:t=fill",
                 "drawbox=x=0:y=ih*0.952:w=iw:h=ih*0.048:color=black:t=fill",
                 # a dark interface under a strong vignette measures 3/255 — the same trap as
                 # the letterbox: dress the frame, do not bury the picture
                 "vignette=PI/8"]
    if body.get("grade", True):
        filt += ["eq=contrast=1.08:saturation=1.05:brightness=0.05", "noise=alls=4:allf=t"]

    SHOTS_OUT.mkdir(parents=True, exist_ok=True)
    dest = SHOTS_OUT / f"{bid}.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
                        "-t", f"{seconds:.3f}", "-vf", ",".join(filt), "-an",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"footage cut failed: {r.stderr[-400:]}")

    beat["renderer"] = "capture"
    beat["capture"] = {"name": capture, "start": start, "seconds": seconds}
    _save(fid, data)
    return {"film": fid, "beat": bid, "renderer": "capture", "output": str(dest),
            "seconds": seconds, "start": start, "bytes": dest.stat().st_size}


@router.post("/films/{fid}/beats/{bid}/render")
def render_shot(fid: str, bid: str):
    data = _load(fid)
    beat = next((b for b in data.get("beats") or [] if b.get("id") == bid), None)
    if not beat:
        raise HTTPException(status_code=404, detail=f"no beat {bid} in {fid}")
    if (beat.get("renderer") or "html").lower() == "comfy":
        raise HTTPException(status_code=400,
                            detail=f"beat {bid} is rendered by a model: POST /comfy/beat")
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
        "gate.check_motion(f),gate.check_audio(f),gate.check_legibility(%r)])))"
        % (str(MOTION / "tools"), str(FILM_DIR))
    )
    r = subprocess.run(["python3", "-c", code, str(video)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=(r.stdout + r.stderr)[-1500:])
    return {"video": video.name, **json.loads(r.stdout.strip().splitlines()[-1])}


@router.post("/films/{fid}/drafts")
def make_drafts(fid: str, body: dict | None = None):
    """Render several cheap directions of the same film and cut each one, music included.

    Returns a job id: three drafts of a 24 s film take a few minutes, and blocking an HTTP call
    that long is how UIs look broken.
    """
    from . import drafts as draft_mod

    body = body or {}
    names = [n for n in (body.get("variants") or []) if n] or list(draft_mod.DEFAULT)
    unknown = [n for n in names if n not in draft_mod.VARIANTS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown variant(s): {', '.join(unknown)}")
    data = _load(fid)
    total = float(data.get("total") or
                  sum(float(b.get("duration") or 0) for b in data.get("beats") or []))

    jid = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[jid] = {"id": jid, "film": fid, "kind": "drafts", "state": "running",
                      "started": time.time(), "variants": names, "tail": ""}

    def work():
        from .music import bed_from_track, find

        root = RENDERS / "drafts"
        bed = None
        if data.get("music"):
            track = find(str(data["music"].get("track_id")))
            if track:
                bed = RENDERS / f"bed-{fid}.wav"
                if not bed.is_file():
                    bed_from_track(track, bed, total,
                                   start=float(data["music"].get("start", 0.0)),
                                   fade_in=float(data["music"].get("fade_in", 0.6)),
                                   fade_out=float(data["music"].get("fade_out", 1.0)))
        written = []
        for name in names:
            vdir = root / f"{fid}-{name}"
            project = draft_mod.build(fid, name, data, FILM_DIR,
                                      FILM_DIR / "drafts" / f"{fid}-{name}")
            raw = vdir / "raw.mp4"
            draft_mod.render_variant(project, raw)
            out = root / f"{fid}-draft-{name}.mp4"
            draft_mod.assemble([raw], out, bed=bed)
            draft_mod.note(fid, name, [out], vdir)
            written.append({"variant": name, "label": draft_mod.VARIANTS[name]["label"],
                            "output": out.name, "bytes": out.stat().st_size})
        return written

    def runner():
        try:
            result = work()
            with _lock:
                _jobs[jid].update({"state": "done", "ok": True, "result": result,
                                   "seconds": round(time.time() - _jobs[jid]["started"], 1)})
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _jobs[jid].update({"state": "failed", "ok": False, "tail": str(exc)})

    threading.Thread(target=runner, daemon=True).start()
    return {"job": jid, "variants": names, "seconds": total}


@router.post("/films/{fid}/treatment")
def set_treatment(fid: str, body: dict):
    """Promote a draft's treatment onto the film itself.

    Drafts are experiments; this is the decision. The previous compositions are copied to
    `film/.backup/<film>-<timestamp>/` first, because "revert" should never depend on memory.
    """
    from . import drafts as draft_mod

    name = (body or {}).get("treatment") or (body or {}).get("name")
    if name not in draft_mod.VARIANTS:
        raise HTTPException(status_code=400,
                            detail=f"unknown treatment {name!r}; known: {', '.join(draft_mod.VARIANTS)}")
    variant = draft_mod.VARIANTS[name]
    data = _load(fid)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = FILM_DIR / ".backup" / f"{fid}-{stamp}"
    changed, skipped = [], []
    for beat in data.get("beats") or []:
        src_rel = beat.get("src") or f"compositions/{beat['id']}.html"
        src = (FILM_DIR / src_rel).resolve()
        if not src.is_file():
            skipped.append(beat["id"])
            continue
        backup.mkdir(parents=True, exist_ok=True)
        (backup / src.name).write_text(src.read_text())
        html = draft_mod._repalette(src.read_text(), variant)
        marker = (variant["css"].strip().splitlines() or [""])[0]
        if variant["css"] and marker and marker not in html and "</style>" in html:
            html = html.replace("</style>", variant["css"] + "  </style>", 1)
        src.write_text(html)
        changed.append(beat["id"])
    data["treatment"] = name
    _save(fid, data)
    return {"film": fid, "treatment": name, "changed": changed,
            "skipped": skipped, "backup": str(backup)}


@router.get("/capture/record/targets")
def record_targets():
    """What a recording can be aimed at: the whole screen, an app window, or a region."""
    from .recorder import targets
    return targets()


@router.post("/capture/record/start")
def record_start(body: dict):
    """Start a desktop recording. Returns at once; the file is finalised by /stop."""
    from .recorder import start

    geo = body.get("geometry")
    if not geo and body.get("window"):
        want = str(body["window"]).lower()
        from .capture import windows
        hit = next((w for w in windows()
                    if want in w["title"].lower() or want == w["id"].lower()), None)
        if not hit:
            raise HTTPException(400, detail=f"no window matching {body['window']!r} — GET /capture/record/targets")
        geo = hit["geometry"]
    name = body.get("name") or f"desktop-{int(time.time())}"
    out = RENDERS / "captures" / f"{name}.mp4"
    result = start(out, geometry=geo, seconds=body.get("seconds"),
                   audio=bool(body.get("audio")), label=body.get("label") or name)
    if not result.get("ok"):
        raise HTTPException(409, detail=result)
    return result


@router.get("/capture/record/status")
def record_status():
    from .recorder import status
    return status()


@router.post("/capture/record/stop")
def record_stop():
    from .recorder import stop
    result = stop()
    if not result.get("ok"):
        raise HTTPException(400, detail=result)
    return result


@router.post("/capture/record/forget")
def record_forget():
    from .recorder import forget
    return forget()


@router.get("/capture/deps")
def capture_deps():
    """What is installed, what is missing, and when the missing piece matters."""
    from .capture import deps
    return deps()


@router.post("/capture/install")
def capture_install(body: dict | None = None):
    """Offer to install the desktop recorder — quietly if allowed, else via a system prompt."""
    from .capture import install_desktop_recorder
    result = install_desktop_recorder(use_prompt=bool((body or {}).get("prompt", True)))
    if not result.get("ok"):
        return {"ok": False, **result}
    return result


@router.get("/capture/windows")
def capture_windows():
    """What could be filmed on this desktop, with geometry."""
    from .capture import windows
    return {"windows": windows()}


@router.post("/capture/desktop")
def capture_desktop(body: dict | None = None):
    """Record the desktop (or one window's region) — for apps that are not web pages.

    Refuses with the install command when `wf-recorder` is missing rather than returning an empty
    file that looks like a successful recording.
    """
    from .capture import desktop as record_desktop, windows

    body = body or {}
    name = _clean(body.get("name") or "desktop", "capture name")
    seconds = float(body.get("seconds", 10.0))
    region = body.get("region")
    if not region and body.get("window"):
        match = [w for w in windows() if body["window"].lower() in w["title"].lower()]
        if not match:
            raise HTTPException(status_code=404, detail=f"no window matching {body['window']!r}")
        region = match[0]["geometry"]
    out = RENDERS / "captures" / f"{name}.mp4"
    result = record_desktop(out, seconds=seconds, region=region)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return {"capture": name, "region": region, **result}


@router.post("/capture/run")
def capture_run(body: dict):
    """Record a page being used. Returns a job: a capture is a minute, not a millisecond."""
    from .capture import record as do_record, studio_walkthrough

    url = (body or {}).get("url") or "http://127.0.0.1:36659/"
    name = _clean((body or {}).get("name") or "capture", "capture name")
    out = RENDERS / "captures" / f"{name}.mp4"
    steps = (body or {}).get("steps")
    walkthrough = bool((body or {}).get("walkthrough"))

    jid = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[jid] = {"id": jid, "kind": "capture", "state": "running",
                      "started": time.time(), "tail": ""}

    def work():
        if walkthrough or not steps:
            return studio_walkthrough(url, out)
        return do_record(url, steps, out)

    def runner():
        try:
            result = work()
            with _lock:
                _jobs[jid].update({"state": "done", "ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _jobs[jid].update({"state": "failed", "ok": False, "tail": str(exc)})

    threading.Thread(target=runner, daemon=True).start()
    return {"job": jid, "output": str(out), "url": url}


@router.get("/films/{fid}/drafts")
def list_drafts(fid: str):
    _film_path(fid)
    root = RENDERS / "drafts"
    if not root.is_dir():
        return {"film": fid, "drafts": []}
    out = []
    for p in sorted(root.glob(f"{fid}-draft-*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        out.append({"name": p.name, "variant": p.stem.rsplit("-", 1)[-1],
                    "bytes": p.stat().st_size, "mtime": int(p.stat().st_mtime)})
    return {"film": fid, "drafts": out}


@router.get("/films/{fid}/drafts/{variant}/video")
def draft_video(fid: str, variant: str):
    _film_path(fid)
    p = RENDERS / "drafts" / f"{fid}-draft-{_clean(variant, 'variant')}.mp4"
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"no draft {variant} for {fid}")
    return FileResponse(str(p), media_type="video/mp4")


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


@router.get("/models")
def list_models():
    """Every model this studio can ask, with a live reachability check."""
    from .review import load_models, probe
    models = load_models(MOTION)
    return {"models": [{**m, "status": probe(m)} for m in models]}


@router.post("/review")
def review_moment(body: dict):
    """Ask a model what is wrong with ONE moment, with measurements and the shot's source.

    Everything the model is given is measured or quoted: nothing is described to it in
    adjectives it could have written itself.
    """
    from . import review as rv

    body = body or {}
    fid = _clean(body.get("film") or "", "film")
    at = float(body.get("time", 0.0))
    note = (body.get("note") or "").strip()
    model_id = body.get("model") or "deepseek-lane"
    want_image = bool(body.get("image", True))

    data = _load(fid)
    video = RENDERS / f"{fid}-final.mp4"
    if not video.is_file():
        video = RENDERS / f"{fid}-raw.mp4"
    if not video.is_file():
        raise HTTPException(status_code=404, detail=f"no render for {fid} — assemble the cut first")

    # which beat is this moment inside?
    t = 0.0
    beat = None
    for b in data.get("beats") or []:
        d = float(b.get("duration") or 0)
        if t <= at < t + d:
            beat = {**b, "_start": t}
            break
        t += d
    if beat is None:
        beat = {**(data.get("beats") or [{}])[-1], "_start": max(0.0, at)}

    src_path = FILM_DIR / (beat.get("src") or f"compositions/{beat.get('id')}.html")
    source = src_path.read_text() if src_path.is_file() else "(this beat has no composition source)"
    if len(source) > 9000:
        source = source[:9000] + "\n… (truncated)"

    motion = rv.motion_profile(video)
    stats = rv.frame_stats(video, at)
    around = motion[max(0, int(at) - 1): int(at) + 2] if motion else []

    frame = None
    if want_image:
        frame = rv.grab_frame(video, at, RENDERS / "reviews" / f"{fid}-{at:.2f}s.jpg")

    model = rv.find_model(model_id, MOTION)
    prompt = rv.PROMPT.format(
        title=data.get("title") or fid,
        seconds=float(data.get("total") or 0),
        beats=len(data.get("beats") or []),
        beat=beat.get("id", "?"),
        beat_seconds=float(beat.get("duration") or 0),
        beat_start=float(beat.get("_start") or 0),
        shot_note=(beat.get("shot") or beat.get("line") or "")[:400],
        at=at, note=note or "(no note — review the moment as it stands)",
        motion=", ".join(f"{v}%/s" for v in around) or "unmeasured",
        luma=stats.get("luma", 0), contrast=stats.get("contrast", 0),
        colour=stats.get("colour", 0), edges=stats.get("edges", 0),
        gate=_gate_summary(fid, video),
        source=source,
    )
    answer = rv.ask(model, prompt, image=frame if model.get("vision") else None)
    payload = rv.parse_json(answer.get("text", "")) if answer.get("ok") else None
    return {"film": fid, "at": at, "beat": beat.get("id"), "note": note,
            "model": model_id, "measurements": {**stats, "motion_around": around},
            "frame": str(frame) if frame else None,
            "image_sent": answer.get("image_sent", False),
            "ok": answer.get("ok"), "error": answer.get("error"),
            "review": payload, "raw": (answer.get("text") or "")[:2000]}


def _gate_summary(fid: str, video: Path) -> str:
    try:
        r = subprocess.run(["python3", "-c",
                            "import json,sys;sys.path.insert(0,%r);import gate;"
                            "f=sys.argv[1];print(json.dumps(gate.verdict([gate.check_hook(f),"
                            "gate.check_motion(f),gate.check_audio(f),gate.check_legibility(%r)])))"
                            % (str(MOTION / "tools"), str(FILM_DIR)), str(video)],
                           capture_output=True, text=True, timeout=300)
        v = json.loads(r.stdout.strip().splitlines()[-1])
        return "; ".join(f"{c['check']} {'pass' if c['pass'] else 'FAIL'} ({c['detail']})"
                         for c in v.get("checks", []))
    except Exception:  # noqa: BLE001
        return "unavailable"


@router.get("/version")
def version():
    """The build stamp: release target, dev build number, commit and date."""
    from . import build_info
    info = build_info.read()
    return {**info, "chip": build_info.chip()}


@router.get("/neighbour/music-studio")
def music_studio_url():
    """Where Music Studio is running, so the studio can hand the user over to it.

    Discovered from that plugin's own state file rather than hard-coded: both plugins can be
    moved, and a link to a dead port is worse than no link.
    """
    import urllib.request

    state = Path.home() / ".hermes" / "plugin-data" / "hermes-music-studio" / "studio-host.json"
    if not state.is_file():
        raise HTTPException(status_code=503, detail="Music Studio has not been started yet")
    try:
        port = int(json.loads(state.read_text()).get("port"))
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=503, detail="Music Studio's state file is unreadable") from None
    url = f"http://127.0.0.1:{port}"
    try:
        urllib.request.urlopen(url, timeout=3)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Music Studio is not answering on {port}") from None
    return {"url": url, "plugin": "hermes-music-studio"}


@router.get("/music/library")
def music_library():
    """Tracks Music Studio has rendered. Read-only: this never touches that plugin's store."""
    from .music import library, store
    try:
        tracks = library()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"music store unreadable: {exc}") from None
    return {"store": str(store()), "tracks": tracks}


@router.post("/films/{fid}/music")
def attach_music(fid: str, body: dict):
    """Choose the film's bed from the music library — or clear it."""
    from .music import find

    data = _load(fid)
    if body.get("track_id") in (None, "", "none"):
        data.pop("music", None)
        _save(fid, data)
        return {"film": fid, "music": None}
    track = find(str(body["track_id"]))
    if track is None:
        raise HTTPException(status_code=404, detail=f"no track {body['track_id']} in the music library")
    data["music"] = {
        "track_id": track["id"],
        "title": track["title"],
        "start": float(body.get("start", 0.0)),
        "fade_in": float(body.get("fade_in", 0.6)),
        "fade_out": float(body.get("fade_out", 1.0)),
    }
    _save(fid, data)
    return {"film": fid, "music": data["music"], "track": track}


@router.post("/films/{fid}/assemble")
def assemble_film(fid: str, body: dict | None = None):
    """Cut the film from its beat clips with MLT, whoever rendered them.

    Assumes `renders/shots/<beat id>.mp4` exists for every beat (HyperFrames shot or H3 clip);
    a missing clip is reported by id instead of being papered over with a still frame.
    """
    from .mlt_project import from_beats, normalise_audio, render as mlt_render

    body = body or {}
    data = _load(fid)
    beats = data.get("beats") or []
    total = float(data.get("total") or sum(float(b.get("duration") or 0) for b in beats))
    missing = [b["id"] for b in beats if not (SHOTS_OUT / f"{b['id']}.mp4").is_file()]
    if missing:
        raise HTTPException(400, f"not rendered yet: {', '.join(missing)}")

    bed = None
    music_note = None
    if data.get("music"):
        from .music import bed_from_track, find

        track = find(str(data["music"].get("track_id")))
        if track is None:
            raise HTTPException(400, detail=f"music track {data['music'].get('track_id')} is gone from the library")
        bed = RENDERS / f"bed-{fid}.wav"
        music_note = bed_from_track(track, bed, total,
                                    start=float(data["music"].get("start", 0.0)),
                                    fade_in=float(data["music"].get("fade_in", 0.6)),
                                    fade_out=float(data["music"].get("fade_out", 1.0)))
    if bed is None:
        for cand in (RENDERS / f"bed-{int(round(total))}.wav", RENDERS / "bed.wav"):
            if cand.is_file():
                bed = cand
                break

    project = from_beats(beats, SHOTS_OUT, bed=bed, total=total,
                         title=fid, dissolve=float(body.get("dissolve", 0.0)))
    out = RENDERS / f"{fid}-final.mp4"
    res = mlt_render(project, out)
    audio = normalise_audio(out) if bed else {"audio": "no bed"}
    return {"film": fid, "output": out.name, "seconds": total,
            "clips": len(project.video()), "bed": bed.name if bed else None,
            "music": music_note, **res, **audio}
