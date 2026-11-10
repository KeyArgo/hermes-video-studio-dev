"""The editing engine: an MLT project written from a film, rendered by melt.

Why MLT: it is the engine behind Shotcut and Kdenlive, it is installed here (7.38), and it
gives a real timeline — tracks, blanks, dissolves, keyframes, audio mixing — from plain XML.
Why OTIO: the project must outlive this tool, so a timeline round-trips through
OpenTimelineIO (optional import; the renderer never depends on it).

The model is deliberately small and explicit: clips with source in/out placed at a start
time, one audio bed, optional dissolves between neighbouring video tracks. Everything the
UI shows about timing comes from this model, never from a guess.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

FPS = 30
WIDTH, HEIGHT = 1920, 1080


@dataclass
class Clip:
    """One piece of picture or sound on the timeline."""
    resource: str
    start: float          # timeline position, seconds
    duration: float       # timeline length after trimming, seconds
    source_in: float = 0.0
    kind: str = "video"   # "video" | "audio"
    label: str = ""

    def frames(self, fps: int = FPS) -> tuple[int, int, int]:
        return (round(self.start * fps), round(self.duration * fps),
                round(self.source_in * fps))


@dataclass
class Dissolve:
    """A crossfade over the overlap of two neighbouring video tracks."""
    at: float             # timeline position of the overlap start
    duration: float

    def frames(self, fps: int = FPS) -> tuple[int, int]:
        start = round(self.at * fps)
        return start, start + round(self.duration * fps) - 1


@dataclass
class Project:
    title: str = "untitled"
    clips: list[Clip] = field(default_factory=list)
    dissolves: list[Dissolve] = field(default_factory=list)
    total: float = 0.0
    fps: int = FPS

    def video(self) -> list[Clip]:
        return sorted((c for c in self.clips if c.kind == "video"), key=lambda c: c.start)

    def audio(self) -> list[Clip]:
        return sorted((c for c in self.clips if c.kind == "audio"), key=lambda c: c.start)

    def to_dict(self) -> dict:
        return {"title": self.title, "total": self.total, "fps": self.fps,
                "clips": [asdict(c) for c in self.clips],
                "dissolves": [asdict(d) for d in self.dissolves]}


# --------------------------------------------------------------------- build
def from_beats(beats: list[dict], clips_dir: Path, bed: Path | None = None,
               total: float | None = None, title: str = "film",
               dissolve: float = 0.0) -> Project:
    """Place one rendered shot per beat, end to end, with an optional audio bed.

    A beat whose clip is missing is skipped rather than replaced by a still: a film that
    silently contains a hole is worse than one that is short.
    """
    project = Project(title=title)
    at = 0.0
    for beat in beats:
        path = Path(clips_dir) / f"{beat['id']}.mp4"
        if not path.is_file():
            at += float(beat.get("duration", 0))
            continue
        dur = float(beat.get("duration", 0)) or _duration(path)
        project.clips.append(Clip(resource=str(path.resolve()), start=at, duration=dur,
                                  label=beat.get("id", "")))
        at += dur
    if bed and Path(bed).is_file():
        span = total if total is not None else at
        project.clips.append(Clip(resource=str(Path(bed).resolve()), start=0.0,
                                  duration=span, kind="audio", label="bed"))
    project.total = total if total is not None else at
    if dissolve > 0:
        vids = project.video()
        for a, b in zip(vids, vids[1:]):
            overlap = (a.start + a.duration) - b.start
            if overlap > 0:
                project.dissolves.append(Dissolve(at=b.start, duration=min(dissolve, overlap)))
    return project


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


# ----------------------------------------------------------------------- mlt
def to_mlt(project: Project) -> str:
    """A multitrack MLT XML project: one video track per clip, dissolves between them."""
    fps = project.fps
    root = ET.Element("mlt", {"LC_NUMERIC": "C", "version": "7.38.0", "title": project.title,
                              "producer": "tractor0"})
    ET.SubElement(root, "profile", {
        "description": "HD 1080p 30 fps", "width": str(WIDTH), "height": str(HEIGHT),
        "progressive": "1", "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": "16", "display_aspect_den": "9",
        "frame_rate_num": str(fps), "frame_rate_den": "1", "colorspace": "709",
    })

    producers: list[tuple[str, Clip, int]] = []
    for i, clip in enumerate(project.video() + project.audio()):
        start_f, dur_f, in_f = clip.frames(fps)
        producer_id = f"producer{i}"
        attrs = {"id": producer_id, "in": str(in_f), "out": str(in_f + dur_f - 1)}
        prod = ET.SubElement(root, "producer", attrs)
        ET.SubElement(prod, "property", {"name": "resource"}).text = clip.resource
        if clip.kind == "audio":
            ET.SubElement(prod, "property", {"name": "set.test_audio"}).text = "0"
        producers.append((producer_id, clip, start_f))

    # base track: MLT leaves unfilled regions of the tractor as white unless a black
    # producer sits underneath — a film with a gap must read as black, not as a flash.
    black = ET.SubElement(root, "producer", {"id": "black", "in": "0", "out": str(max(0, round(project.total * fps) - 1))})
    ET.SubElement(black, "property", {"name": "mlt_service"}).text = "colour"
    ET.SubElement(black, "property", {"name": "resource"}).text = "black"
    black_pl = ET.SubElement(root, "playlist", {"id": "playlist_black"})
    ET.SubElement(black_pl, "entry", {"producer": "black", "in": "0", "out": str(max(0, round(project.total * fps) - 1))})

    playlists = []
    for i, (producer_id, clip, start_f) in enumerate(producers):
        pid = f"playlist{i}"
        pl = ET.SubElement(root, "playlist", {"id": pid})
        if start_f > 0:
            ET.SubElement(pl, "blank", {"length": str(start_f)})
        start_f, dur_f, in_f = clip.frames(fps)
        ET.SubElement(pl, "entry", {"producer": producer_id, "in": str(in_f),
                                    "out": str(in_f + dur_f - 1)})
        playlists.append((pid, clip))

    total_f = max((s + d for _, _, s in [(pid, c, *c.frames(fps)[:1]) for pid, c in playlists] for d in [0]), default=0)
    total_frames = round(project.total * fps) - 1 if project.total else 0
    if total_frames <= 0:
        total_frames = max(c.frames(fps)[0] + c.frames(fps)[1] for _, c in playlists) - 1

    tractor = ET.SubElement(root, "tractor", {"id": "tractor0", "in": "0", "out": str(total_frames)})
    ET.SubElement(tractor, "property", {"name": "global_feed"}).text = "1"
    ET.SubElement(tractor, "track", {"producer": "playlist_black"})

    video_tracks, audio_tracks = [], []
    for pid, clip in playlists:
        track = ET.SubElement(root, "track", {"producer": pid})
        (video_tracks if clip.kind == "video" else audio_tracks).append((pid, clip))
        # the tractor must reference every track in order
        ET.SubElement(tractor, "track", {"producer": pid})

    # dissolves between consecutive video tracks
    for d in project.dissolves:
        in_f, out_f = d.frames(fps)
        idx = _track_index(d.at, video_tracks, fps)
        if idx is None or idx + 1 >= len(video_tracks):
            continue
        tr = ET.SubElement(tractor, "transition", {"in": str(in_f), "out": str(out_f)})
        for name, value in (("a_track", str(idx + 1)), ("b_track", str(idx + 2)),
                            ("mlt_service", "mix"), ("start", "1"), ("end", "0")):
            ET.SubElement(tr, "property", {"name": name}).text = value

    ET.indent(root, space="  ")
    return '<?xml version="1.0" standalone="no"?>\n' + ET.tostring(root, encoding="unicode")


def _track_index(at: float, video_tracks: list, fps: int) -> int | None:
    """Index of the video track whose clip starts at ``at`` (the incoming clip)."""
    for i, (_, clip) in enumerate(video_tracks):
        if abs(clip.start - at) < 1.0 / fps:
            return max(0, i - 1)
    return None


# --------------------------------------------------------------------- render
def render(project: Project, out: Path, workdir: Path | None = None,
           quality: str = "delivery") -> dict:
    if not shutil.which("melt"):
        raise RuntimeError("melt is not installed (MLT framework)")
    out.parent.mkdir(parents=True, exist_ok=True)
    xml_path = (workdir or out.parent) / f"{out.stem}.mlt"
    xml_path.write_text(to_mlt(project), encoding="utf-8")

    consumer = ["-consumer", "avformat:" + str(out),
                "vcodec=libx264", "preset=medium", "crf=18" if quality == "delivery" else "crf=23",
                "acodec=aac", "ab=192k", "ar=48000", "f=mp4",
                "rescale=bicubic", "real_time=-4"]
    proc = subprocess.run(["melt", str(xml_path), *consumer], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("melt failed:\n" + (proc.stderr or proc.stdout)[-1500:])
    return {"out": str(out), "xml": str(xml_path), "seconds": _duration(out),
            "bytes": out.stat().st_size}


# ----------------------------------------------------------------------- otio
def _measure_loudness(path: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                        "loudnorm=I=-16:TP=-1.5:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    import re
    import json as _json
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return {}
    d = _json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "tp": float(d["input_tp"])}


def normalise_audio(path: Path, low: float = -18.0, high: float = -14.0,
                    target: float = -16.0) -> dict:
    """Bring a rendered cut's audio into the delivery band, in place.

    `melt` mixes a bed at its file level — a quiet bed arrives 20 dB down and plays as silence
    on most systems. The gate now measures audio, so the fix belongs here rather than in a
    manual ffmpeg step after every cut.
    """
    before = _measure_loudness(path)
    if not before:
        return {"audio": "none"}
    if low <= before["lufs"] <= high:
        return {"audio": "already in band", **before}
    tmp = path.with_suffix(".norm.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                        "-c:v", "copy", "-af",
                        f"loudnorm=I={target}:TP=-1.5:LRA=11",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"audio normalise failed: {r.stderr[-300:]}")
    tmp.replace(path)
    after = _measure_loudness(path)
    return {"audio": "normalised", "before_lufs": before["lufs"], "after_lufs": after.get("lufs")}


def to_otio(project: Project):
    """OpenTimelineIO timeline (optional dependency) — the project that outlives this tool."""
    import opentimelineio as otio

    timeline = otio.schema.Timeline(name=project.title)
    rate = float(project.fps)
    v_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    for clip in project.video():
        v_track.append(otio.schema.Clip(
            name=clip.label or Path(clip.resource).stem,
            media_reference=otio.schema.ExternalReference(target_url=clip.resource),
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(clip.source_in * rate, rate),
                otio.opentime.RationalTime(clip.duration * rate, rate)),
        ))
    for clip in project.audio():
        a_track.append(otio.schema.Clip(
            name=clip.label or Path(clip.resource).stem,
            media_reference=otio.schema.ExternalReference(target_url=clip.resource),
            source_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, rate),
                otio.opentime.RationalTime(clip.duration * rate, rate)),
        ))
    timeline.tracks.extend([v_track, a_track])
    return timeline


def from_otio(timeline) -> Project:
    """Read a timeline back into the render model, so an outside edit can be rendered."""
    project = Project(title=timeline.name or "untitled")
    at_by_kind: dict[str, float] = {"video": 0.0, "audio": 0.0}
    for track in timeline.tracks:
        kind = "video" if track.kind == "Video" else "audio"
        at = 0.0
        for item in track:
            if hasattr(item, "source_range") and item.source_range is not None and hasattr(item, "media_reference"):
                rate = float(item.source_range.duration.rate or project.fps)
                dur = item.source_range.duration.value / rate
                sin = item.source_range.start_time.value / rate
                project.clips.append(Clip(resource=item.media_reference.target_url,
                                          start=at, duration=dur, source_in=sin, kind=kind,
                                          label=item.name or ""))
                at += dur
            elif hasattr(item, "duration"):
                rate = float(project.fps)
                at += item.duration.value / rate
    project.total = max((c.start + c.duration for c in project.clips), default=0.0)
    return project


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Render a film through MLT from its shot clips.")
    ap.add_argument("--film", required=True, help="films/<id>.json")
    ap.add_argument("--clips", required=True, help="directory of per-shot mp4s")
    ap.add_argument("--bed", default=None, help="audio bed wav")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dissolve", type=float, default=0.0)
    ap.add_argument("--otio", default=None, help="also write an .otio timeline here")
    a = ap.parse_args()

    film = json.loads(Path(a.film).read_text())
    proj = from_beats(film["beats"], Path(a.clips), Path(a.bed) if a.bed else None,
                      total=film.get("total"), title=film.get("title", "film"),
                      dissolve=a.dissolve)
    if a.otio:
        tl = to_otio(proj)
        tl.to_json_file(a.otio)
        print("otio:", a.otio, "clips:", len(proj.clips))
    print(json.dumps(render(proj, Path(a.out)), indent=2))
