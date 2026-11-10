"""Rough drafts: several directions of the same film, cheap enough to watch all of them.

A draft exists to answer "which of these do I want to spend time on", so it must be fast and it
must be honestly different — three tints of one design answers nothing. Each variant changes
the *treatment* (how the frame is composed) and the palette, and is rendered at half frame rate
and quarter resolution, which cuts render time by roughly 4x.

What a draft is not: shippable. Drafts are not gated, and they are never copied over a real
render. The filenames say `-draft-`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

VARIANTS: dict[str, dict] = {
    "quiet": {
        "label": "Quiet — as authored",
        "accent": "#3b82f6", "bg": "#0b0d10", "ink": "#e8eaed", "dim": "#8b929c",
        "css": "",
    },
    "cinematic": {
        "label": "Cinematic — letterbox, vignette, unhurried",
        "accent": "#8fb6ff", "bg": "#07080a", "ink": "#f1f3f5", "dim": "#7c848e",
        "css": """
    /* letterbox: the frame stops being a slide and starts being a film */
    #root::before { content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 90;
      box-shadow: inset 0 0 220px 60px rgba(0,0,0,.85); }
    #root::after { content: ""; position: fixed; left: 0; right: 0; height: 6.2%;
      background: #000; z-index: 91; pointer-events: none;
      top: 0; box-shadow: 0 93.8vh 0 0 #000; }
""",
    },
    "kinetic": {
        "label": "Kinetic — cut hard, move more, label less",
        "accent": "#f59e0b", "bg": "#0a0a0b", "ink": "#f4f4f5", "dim": "#9aa1aa",
        "css": """
    /* a hard rule and a running accent: reads as energy without new content */
    #root::before { content: ""; position: absolute; left: 0; right: 0; top: 11.5%; height: 3px;
      background: linear-gradient(90deg, transparent, rgba(245,158,11,.9), transparent);
      z-index: 88; pointer-events: none; }
    #root::after { content: ""; position: absolute; left: 4.2vw; bottom: 5.4vh; width: 9px; height: 9px;
      background: #f59e0b; z-index: 88; pointer-events: none; }
""",
    },
}

DEFAULT = ["quiet", "cinematic", "kinetic"]

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

# the palette the shots were authored with — anything else is content (a product's colours)
_AUTHORED = {"#0b0d10": "bg", "#e8eaed": "ink", "#8b929c": "dim", "#3b82f6": "accent",
             "#f59e0b": "accent"}


def _repalette(html: str, variant: dict) -> str:
    """Swap brand colours only. Content colours (a game's own palette) are left alone."""
    def swap(m: re.Match) -> str:
        role = _AUTHORED.get(m.group(0).lower())
        return variant.get(role, m.group(0)) if role else m.group(0)
    return _HEX.sub(swap, html)


def build(fid: str, variant_name: str, film: dict, film_dir: Path, project_dir: Path) -> Path:
    """Write a standalone variant project: its own index.html plus recoloured compositions.

    HyperFrames only renders compositions that are mounted from an `index.html`, and only from
    inside that project, so a draft is a small project of its own rather than a loose file —
    which also keeps the real film's index.html untouched.
    """
    variant = VARIANTS[variant_name]
    index_src = film_dir / "index.html"
    if not index_src.is_file():
        raise RuntimeError(f"no index.html in {film_dir}: build the film first")
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "compositions").mkdir(exist_ok=True)

    for beat in film.get("beats") or []:
        src_rel = beat.get("src") or f"compositions/{beat['id']}.html"
        src = (film_dir / src_rel).resolve()
        if not src.is_file():
            continue  # generative beats are not part of a treatment draft
        html = _repalette(src.read_text(), variant)
        if variant["css"] and "</style>" in html:
            html = html.replace("</style>", variant["css"] + "  </style>", 1)
        (project_dir / "compositions" / src.name).write_text(html)

    (project_dir / "index.html").write_text(index_src.read_text())
    return project_dir


def render_variant(project_dir: Path, out: Path, hf: str = "hyperframes@0.9.26",
                   fps: int = 15) -> Path:
    """Render the whole variant in one pass at half frame rate and draft encoder settings."""
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["npx", "--yes", hf, "render", "--quality", "draft", "--fps", str(fps),
                        "--output", str(out.resolve())],
                       capture_output=True, text=True, cwd=str(project_dir))
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError(f"draft render failed: {(r.stdout + r.stderr)[-500:]}")
    return out


def assemble(clips: list[Path], dest: Path, bed: Path | None = None,
             width: int = 960, height: int = 540) -> dict:
    """Concatenate the variant's clips and lay the film's music under them.

    ffmpeg's concat is used rather than MLT here: drafts are throwaway and this is one pass,
    while a draft that needs a timeline project would cost more to build than to watch.
    """
    if not clips:
        raise RuntimeError("no clips to assemble")
    args = ["ffmpeg", "-y", "-v", "error"]
    for c in clips:
        args += ["-i", str(c)]
    if bed:
        args += ["-i", str(bed)]
    labels = "".join(f"[{i}:v]" for i in range(len(clips)))
    filt = f"{labels}concat=n={len(clips)}:v=1:a=0,scale={width}:{height}:flags=lanczos[v]"
    args += ["-filter_complex", filt, "-map", "[v]"]
    if bed:
        # a draft is judged with sound, so it gets the same delivery level as a real cut
        args += ["-map", f"{len(clips)}:a", "-shortest",
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                 "-c:a", "aac", "-b:a", "128k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(dest)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"assemble failed: {r.stderr[-400:]}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return {"output": str(dest), "bytes": dest.stat().st_size, "width": width, "height": height}


def note(fid: str, variant: str, files: list[Path], out_dir: Path) -> Path:
    payload = {"film": fid, "variant": variant, "label": VARIANTS[variant]["label"],
               "draft": True, "not_for_delivery": True,
               "clips": [str(f) for f in files]}
    p = out_dir / "draft.json"
    p.write_text(json.dumps(payload, indent=2))
    return p


def cleanup(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
