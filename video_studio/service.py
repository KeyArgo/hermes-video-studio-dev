"""Service layer — every studio operation, callable by the agent tool and the UI.

Both entry points go through these functions so the tool and the interface can
never disagree about what an op does. None of them render unless asked.
"""
from __future__ import annotations

import os
import time

from . import edl, store

MAX_SECONDS = 90.0

PREVIEW_W, PREVIEW_H = 1280, 720


def _preview_filtergraph(tl, has_audio):
    """Build (graph, extra_inputs, maps) for a fast 720p preview.

    Pure string construction — no ffmpeg runs — so it can be unit-tested. It
    mirrors the delivery render in two ways the old inline builder got wrong:

    * **silent sources**: without an audio track, emitting ``[a{i}]`` labels and
      ``concat ...:a=1`` references audio pads that are never defined, so the
      whole preview fails. A silent source now concats ``a=0`` and gets a
      synthesised ``anullsrc`` track, exactly like ``edl.Project.render``.
    * **cards**: a card segment is its own PNG input, not a slice of ``[0:v]`` —
      the old builder trimmed the source footage where the card should be.
    """
    graph, labels = [], []
    cards: dict[str, int] = {}
    for s in tl:
        if s.get("kind") == "card":
            cards.setdefault(s["png"], len(cards) + 1)
    extra_inputs = []
    for png in cards:
        extra_inputs += ["-loop", "1", "-i", png]

    for i, s in enumerate(tl):
        if s.get("kind") == "card":
            k = cards[s["png"]]
            graph.append(
                f"[{k}:v]scale={PREVIEW_W}:{PREVIEW_H}:force_original_aspect_ratio=decrease,"
                f"pad={PREVIEW_W}:{PREVIEW_H}:(ow-iw)/2:(oh-ih)/2:color=0x0b0d10,"
                f"fps={edl.FPS},trim=start={s['src_in']}:end={s['src_out']},"
                f"setpts=PTS-STARTPTS,setsar=1[v{i}]")
            if has_audio:
                graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{s['duration']:.3f},"
                             f"asetpts=PTS-STARTPTS[a{i}]")
        else:
            graph.append(
                f"[0:v]trim=start={s['src_in']}:end={s['src_out']},"
                f"setpts=(PTS-STARTPTS)/{s['speed']},fps={edl.FPS},"
                f"scale={PREVIEW_W}:{PREVIEW_H}:force_original_aspect_ratio=decrease,"
                f"pad={PREVIEW_W}:{PREVIEW_H}:(ow-iw)/2:(oh-ih)/2:color=0x0b0d10,setsar=1[v{i}]")
            if has_audio:
                graph.append(
                    f"[0:a]atrim=start={s['src_in']}:end={s['src_out']},"
                    f"asetpts=PTS-STARTPTS,{edl._atempo(s['speed'])},"
                    f"volume={'0' if s.get('muted') else '1'},"
                    f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
        labels.append(f"[v{i}][a{i}]" if has_audio else f"[v{i}]")

    n = len(tl)
    if has_audio:
        graph.append("".join(labels) + f"concat=n={n}:v=1:a=1[vc][ac]")
    else:
        graph.append("".join(labels) + f"concat=n={n}:v=1:a=0[vc]")
        total = sum(s["duration"] for s in tl)
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f},"
                     f"asetpts=PTS-STARTPTS[ac]")
    maps = ["-map", "[vc]", "-map", "[ac]"]
    return graph, extra_inputs, maps


def snapshot(p: "edl.Project") -> dict:
    tl, caps = p.timeline()
    info = edl.probe(p.source)
    return {
        "title": p.meta.get("title") or p.name,
        "source": p.source,
        "source_duration": round(info["duration"], 2),
        "duration": round(sum(s["duration"] for s in tl), 2),
        "segments": tl,
        "captions": caps,
        "ops": p.ops,
        "op_count": len(p.ops),
        "meta": p.meta,
    }


def project_view(pid: str) -> dict:
    p = store.load(pid)
    view = snapshot(p)
    d = store.project_dir(pid)
    renders = sorted((d / "renders").glob("*.mp4"), key=os.path.getmtime, reverse=True)
    view.update({
        "id": pid,
        "renders": [{"name": r.name, "bytes": r.stat().st_size,
                     "at": int(r.stat().st_mtime)} for r in renders],
    })
    return view


def create(source: str, name: str | None = None, entry: str = "",
           accent: str = "#3b82f6") -> dict:
    pid, p = store.create(source, name, entry, accent)
    return {"id": pid, **snapshot(p)}


def apply_op(pid: str, op: str, **kw) -> dict:
    p = store.load(pid)
    if op == "snip":
        p.snip(float(kw["start"]), float(kw["end"]))
    elif op == "snip_many":
        p.snip_many(kw["ranges"])
    elif op == "tighten":
        # Measured on the TIMELINE, not the source — otherwise any prior snip or
        # speed op makes the cut land on the wrong material.
        a = p.analyze_timeline()
        cuts = a["suggested_tighten"]
        if cuts:
            p.snip_many(cuts)
        store.save(pid, p)
        return {"applied": "tighten", "removed_pauses": len(cuts),
                "removed_seconds": a["would_remove_seconds"], **snapshot(p)}
    elif op == "trim":
        p.trim(kw.get("start"), kw.get("end"))
    elif op == "speed":
        p.speed(float(kw["factor"]), kw.get("start"), kw.get("end"))
    elif op == "mute":
        p.mute(float(kw["start"]), float(kw["end"]))
    elif op == "card":
        from . import cards as cardmod
        kind = str(kw.get("kind") or "title")
        seconds = float(kw.get("seconds") or 1.2)
        cdir = store.project_dir(pid) / "cards"
        cdir.mkdir(exist_ok=True)
        n = len(list(cdir.glob("*.png"))) + 1
        png = cardmod.render_card(
            str(cdir / f"{kind}-{n:02d}.png"), kind,
            title=p.meta.get("title") or p.name,
            claim=str(kw.get("claim") or ""),
            install=str(kw.get("install") or ""),
            limit=str(kw.get("limit") or ""),
            accent=p.meta.get("accent", "#3b82f6"))
        p.card(png, seconds, kw.get("at"), kind)
    elif op == "caption":
        p.caption(str(kw["text"]), float(kw["start"]), float(kw["end"]))
    elif op == "undo":
        gone = p.undo()
        store.save(pid, p)
        return {"applied": "undo", "undone": gone, **snapshot(p)}
    elif op == "drop":
        gone = p.drop(int(kw["index"]))
        store.save(pid, p)
        return {"applied": "drop", "dropped": gone, **snapshot(p)}
    elif op == "edit":
        idx = int(kw.pop("index"))
        merged = p.edit_op(idx, **{k: v for k, v in kw.items() if v is not None})
        store.save(pid, p)
        return {"applied": "edit", "op": merged, **snapshot(p)}
    elif op == "relink":
        src = p.relink(str(kw["source"]))
        store.save(pid, p)
        return {"applied": "relink", "source": src, **snapshot(p)}
    else:
        raise ValueError(f"unknown op {op!r}")
    store.save(pid, p)
    return {"applied": op, **snapshot(p)}


def render(pid: str, fast: bool = False, max_seconds: float = MAX_SECONDS) -> dict:
    p = store.load(pid)
    d = store.project_dir(pid)
    d.joinpath("renders").mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{'preview' if fast else 'cut'}-{stamp}.mp4"
    out = d / "renders" / name

    if fast:
        # A preview only has to be watchable, not shippable: it exists so a person
        # can hear the cut before committing two minutes to a real render.
        tl, caps = p.timeline()
        info = edl.probe(p.source)
        graph, extra_inputs, maps = _preview_filtergraph(tl, info["has_audio"])
        edl._run(["ffmpeg", "-y", "-v", "error", "-i", p.source, *extra_inputs,
                  "-filter_complex", ";".join(graph), *maps,
                  "-c:v", "libx264", "-crf", "30",
                  "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "128k", str(out)])
        check = p.verify(str(out), max_seconds=1e9)
        # A preview is 720p on purpose, so resolution/black checks would report
        # failures that are not defects. Only timing and audio are meaningful here.
        keep = {"duration <= cap", "has audio", "loudness -18..-14 LUFS",
                "no clipping", "no dead air >1.5s"}
        return {"rendered": out.name, "kind": "preview",
                "duration": check["duration"], "shippable": None,
                "checks": [c for c in check["checks"] if c["check"] in keep],
                "failures": []}

    p.render(str(out))
    check = p.verify(str(out), max_seconds=max_seconds)
    return {"rendered": out.name, "kind": "cut", "duration": check["duration"],
            "shippable": check["pass"], "failures": check["failures"],
            "checks": check["checks"]}


def waveform(pid: str, width: int = 1600, height: int = 160) -> str:
    """Cached peak image for the timeline. Regenerated only when the source moves."""
    d = store.project_dir(pid)
    png = d / f"waveform-{width}x{height}.png"
    p = store.load(pid)
    if png.exists() and png.stat().st_mtime >= os.path.getmtime(p.source):
        return str(png)
    edl._run(["ffmpeg", "-y", "-v", "error", "-i", p.source, "-filter_complex",
              f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:"
              f"colors=#5b6472", "-frames:v", "1", str(png)])
    if not png.exists():  # silent source: draw a flat line rather than 404
        edl._run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                  f"color=c=#2a2f38:s={width}x{height}:d=1", "-frames:v", "1", str(png)])
    return str(png)


def analyze(pid: str) -> dict:
    p = store.load(pid)
    return edl.analyze(p.source)
