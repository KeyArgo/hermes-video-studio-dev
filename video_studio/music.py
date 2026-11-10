"""Music Studio -> Video Studio: use a track that already exists instead of a synthetic bed.

Music Studio keeps its projects in `~/.hermes/plugin-data/hermes-music-studio`: one JSON per
project and one WAV per take. This module reads that store (never writes it), so a track made
in the music room can become the bed of a film without an export step.

The two plugins stay independent: nothing here imports Music Studio's code, only its files. If
the store is somewhere else, set `MUSIC_STORE`.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def store() -> Path:
    env = os.environ.get("MUSIC_STORE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hermes" / "plugin-data" / "hermes-music-studio"


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=sample_rate,channels",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    try:
        d = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {}
    s = (d.get("streams") or [{}])[0]
    return {"seconds": round(float((d.get("format") or {}).get("duration") or 0), 2),
            "sample_rate": int(s.get("sample_rate") or 0),
            "channels": int(s.get("channels") or 0)}


def library() -> list[dict]:
    """Every take Music Studio has rendered, newest project first.

    A take is only listed if its WAV is actually on disk: a project whose audio was deleted is
    not a track you can cut to, and pretending otherwise would fail later at assembly.
    """
    root = store()
    if not root.is_dir():
        return []
    tracks: list[dict] = []
    for p in sorted(root.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith(('.', '_')):
            continue
        try:
            project = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(project, dict) or not project.get("project_id"):
            continue
        for take in project.get("takes") or []:
            take_id = take.get("take_id") or take.get("id")
            if not take_id:
                continue
            wav = root / f"{take_id}.wav"
            if not wav.is_file():
                continue
            info = _probe(wav)
            tracks.append({
                "id": take_id,
                "project": project["project_id"],
                "title": project.get("title") or "untitled",
                "bpm": project.get("bpm"),
                "key": project.get("keyscale") or project.get("key"),
                "caption": (project.get("caption") or "")[:160],
                "path": str(wav),
                "approved": bool(take.get("approved") or project.get("approved")),
                **info,
            })
    return tracks


def find(track_id: str) -> dict | None:
    return next((t for t in library() if t["id"] == track_id), None)


def bed_from_track(track: dict, out: Path, seconds: float, start: float = 0.0,
                   fade_in: float = 0.6, fade_out: float = 1.0) -> dict:
    """Cut `seconds` of a track, with fades, as a 48 kHz stereo bed.

    `start` is where in the track the film begins — a 24 s film does not have to start at the
    top of a 90 s piece. If the track is shorter than the film, the take is looped rather than
    left silent for the tail.
    """
    src = Path(track["path"])
    if not src.is_file():
        raise FileNotFoundError(f"track not found: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = float(track.get("seconds") or 0)
    loop = dur and dur < seconds + start
    chain = ["-af", (f"atrim=start={start}:duration={seconds},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:st=0:d={fade_in},"
                     f"afade=t=out:st={max(0.0, seconds - fade_out)}:d={fade_out},"
                     f"aresample=48000,aformat=channel_layouts=stereo")]
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(src), "-t", f"{seconds:.3f}", *chain, "-c:a", "pcm_s16le", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg bed failed: {r.stderr[-300:]}")
    return {"bed": str(out), "from": track["title"], "start": start, "seconds": seconds,
            "looped": bool(loop), **{k: track[k] for k in ("bpm", "key") if k in track}}
