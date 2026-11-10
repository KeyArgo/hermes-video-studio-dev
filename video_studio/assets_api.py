"""Asset intake: screenshots, screen recordings and any other source material.

The studio needs to eat what people actually have — a PNG grabbed from a monitor, a phone
recording, an OBS capture — without a manual ffmpeg command per file. Every asset is
normalised once (proxy, thumbnail, sidecar) and then addressable, so a film beat can point at
an asset id instead of a path.

Rules this file keeps:
  * nothing is destroyed — the original stays where it was, the library copy is derived;
  * every number in a sidecar is probed, never assumed;
  * a still is a first-class source: a screenshot becomes a moving clip (slow push), because a
    paused frame in a video reads as a mistake.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"}

PROXY_HEIGHT = 540
W, H, FPS = 1920, 1080, 30


def _motion() -> Path:
    from .films_api import MOTION
    return MOTION


def library() -> Path:
    path = _motion() / "assets" / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(text: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", Path(text).stem).strip("-").lower() or "asset"
    return base[:60]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def probe(path: Path) -> dict:
    """Facts about a media file, from ffprobe. Unknown fields are absent, not guessed."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", str(path)])
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr.strip()[:200]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    info: dict = {"bytes": int(fmt.get("size") or 0),
                  "container": fmt.get("format_name", ""),
                  "has_audio": audio is not None}
    for key, value in (("duration", fmt.get("duration")),):
        try:
            info[key] = round(float(value), 3)
        except (TypeError, ValueError):
            pass
    if video:
        info.update({"width": video.get("width"), "height": video.get("height"),
                     "codec": video.get("codec_name")})
        try:
            num, den = str(video.get("r_frame_rate", "0/1")).split("/")
            info["fps"] = round(float(num) / float(den or 1), 3) if float(den or 1) else None
        except (ValueError, ZeroDivisionError):
            info["fps"] = None
    if audio:
        info["sample_rate"] = int(audio.get("sample_rate") or 0)
    return info


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "unknown"


# ------------------------------------------------------------------ ingest
def ingest(src: Path) -> dict:
    """Copy a source into the library with a proxy, a thumbnail and a sidecar."""
    src = Path(src).expanduser()
    if not src.is_file():
        raise FileNotFoundError(src)
    kind = kind_of(src)
    if kind == "unknown":
        raise ValueError(f"unsupported file type: {src.suffix}")

    digest = hashlib.sha1(src.read_bytes()[:4 * 1024 * 1024] + str(src.stat().st_size).encode()).hexdigest()[:8]
    asset_id = f"{_slug(src.name)}-{digest}"
    dest_dir = library() / asset_id
    already = (dest_dir / "asset.json").is_file()
    dest_dir.mkdir(parents=True, exist_ok=True)
    master = dest_dir / f"master{src.suffix.lower()}"
    if not master.exists():
        shutil.copy2(src, master)

    info = probe(master)
    record = {"id": asset_id, "kind": kind, "new": not already, "name": src.name,
              "origin": str(src.resolve()),
              "master": str(master), "probe": info, "thumb": None, "proxy": None,
              "clip": None}

    if kind == "image":
        thumb = dest_dir / "thumb.jpg"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(master),
              "-vf", "scale=480:-2:force_original_aspect_ratio=decrease", "-frames:v", "1",
              str(thumb)])
        record["thumb"] = str(thumb) if thumb.exists() else None
    else:
        thumb = dest_dir / "thumb.jpg"
        _run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(master), "-frames:v", "1",
              "-vf", "scale=480:-2:force_original_aspect_ratio=decrease", str(thumb)])
        record["thumb"] = str(thumb) if thumb.exists() else None
        if kind == "video":
            proxy = dest_dir / "proxy.mp4"
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(master),
                  "-vf", f"scale=-2:{PROXY_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "26", "-c:a", "aac", "-b:a", "128k", str(proxy)])
            record["proxy"] = str(proxy) if proxy.exists() else None

    (dest_dir / "asset.json").write_text(json.dumps(record, indent=2))
    return record


def scan(folders: list[Path] | None = None) -> dict:
    """Ingest everything new in the intake folder (and optional extra folders)."""
    intake = _motion() / "assets" / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    seen, added, skipped, failed = set(), [], [], []
    for folder in [intake] + [Path(f).expanduser() for f in (folders or [])]:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or kind_of(path) == "unknown":
                continue
            try:
                record = ingest(path)
                if record["id"] in seen or not record.get("new", True):
                    skipped.append(path.name)
                else:
                    seen.add(record["id"])
                    added.append(record["id"])
            except Exception as exc:          # one bad file must not stop a folder
                failed.append({"file": path.name, "error": str(exc)[:200]})
    return {"added": added, "skipped": skipped, "failed": failed}


def listing() -> list[dict]:
    items = []
    for sidecar in sorted(library().glob("*/asset.json")):
        try:
            record = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        items.append(record)
    return items


def find(asset_id: str) -> dict:
    sidecar = library() / asset_id / "asset.json"
    if not sidecar.is_file():
        raise HTTPException(404, f"no asset {asset_id}")
    return json.loads(sidecar.read_text())


# ------------------------------------------------------------------ stills
def still_clip(asset_id: str, seconds: float = 5.0, zoom: float = 0.06,
               direction: str = "in") -> dict:
    """Turn a screenshot into a moving clip: a slow push (or pull), 1080p30, with room to cut.

    A still that sits motionless for five seconds is the single most common way a demo video
    looks amateur, so the motion is generated here rather than left to the editor.
    """
    record = find(asset_id)
    if record["kind"] != "image":
        raise HTTPException(400, f"{asset_id} is a {record['kind']}, not an image")
    src = Path(record["master"])
    out = Path(record["master"]).parent / f"still-{int(seconds * 1000)}ms-{direction}.mp4"
    frames = int(seconds * FPS)
    # oversize then zoompan keeps the edges clean; s=WxH forces the output frame
    rate = max(0.0, float(zoom)) / max(frames, 1)
    z = f"zoom+{rate:.6f}" if direction == "in" else f"if(lte(zoom,1.0),1.0,zoom-{rate:.6f})"
    vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:"
          f"s={W}x{H}:fps={FPS},format=yuv420p")
    proc = _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src),
                 "-vf", vf, "-t", str(seconds), "-c:v", "libx264", "-preset", "medium",
                 "-crf", "18", "-movflags", "+faststart", str(out)])
    if proc.returncode != 0 or not out.exists():
        raise HTTPException(500, f"still clip failed: {(proc.stderr or '')[-300:]}")
    record["clip"] = str(out)
    (out.parent / "asset.json").write_text(json.dumps(record, indent=2))
    return {"clip": str(out), "seconds": seconds, "probe": probe(out)}


def trim(asset_id: str, start: float, end: float) -> dict:
    """Cut a section out of a recording (frame-accurate re-encode, keeps audio)."""
    record = find(asset_id)
    if record["kind"] != "video":
        raise HTTPException(400, "only video assets can be trimmed")
    if end <= start:
        raise HTTPException(400, "end must be after start")
    src = Path(record["master"])
    out = Path(record["master"]).parent / f"trim-{int(start * 1000)}-{int(end * 1000)}.mp4"
    proc = _run(["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-i", str(src),
                 "-t", str(end - start), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    if proc.returncode != 0 or not out.exists():
        raise HTTPException(500, f"trim failed: {(proc.stderr or '')[-300:]}")
    return {"clip": str(out), "seconds": round(end - start, 3), "probe": probe(out)}


# ------------------------------------------------------------------- routes
@router.post("/assets/upload")
async def post_upload(request: Request, name: str):
    """Take a file straight from the browser (drag-and-drop or a picker) into the library.

    Raw body + a filename in the query string: no multipart dependency, and the file lands in
    the intake folder first so a failed ingest is visible rather than half-imported.
    """
    if kind_of(Path(name)) == "unknown":
        raise HTTPException(400, f"unsupported file type: {Path(name).suffix}")
    intake = _motion() / "assets" / "intake"
    intake.mkdir(parents=True, exist_ok=True)
    safe = _slug(name) + Path(name).suffix.lower()
    dest = intake / safe
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    dest.write_bytes(body)
    try:
        record = ingest(dest)
    except Exception as exc:
        raise HTTPException(500, f"ingest failed: {exc}") from None
    return record


@router.get("/assets")
def get_assets():
    return {"assets": listing(), "library": str(library())}


@router.post("/assets/scan")
def post_scan(body: dict | None = None):
    folders = (body or {}).get("folders") or []
    return scan(folders)


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str):
    return find(asset_id)


@router.get("/assets/{asset_id}/thumb")
def get_thumb(asset_id: str):
    record = find(asset_id)
    if not record.get("thumb") or not Path(record["thumb"]).is_file():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(record["thumb"], media_type="image/jpeg")


@router.get("/assets/{asset_id}/master")
def get_master(asset_id: str):
    record = find(asset_id)
    path = Path(record["master"])
    if not path.is_file():
        raise HTTPException(404, "no master")
    return FileResponse(path)


@router.get("/assets/{asset_id}/proxy")
def get_proxy(asset_id: str):
    record = find(asset_id)
    path = record.get("proxy") or record.get("master")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "no proxy")
    return FileResponse(path, media_type="video/mp4")


@router.post("/assets/{asset_id}/still")
def post_still(asset_id: str, body: dict | None = None):
    body = body or {}
    return still_clip(asset_id, float(body.get("seconds", 5.0)), float(body.get("zoom", 0.06)),
                      str(body.get("direction", "in")))


@router.post("/assets/{asset_id}/trim")
def post_trim(asset_id: str, body: dict):
    return trim(asset_id, float(body["start"]), float(body["end"]))
