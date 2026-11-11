#!/usr/bin/env python3
"""CLI for the edit engine — same operations the agent's tool exposes.

    python3 -m video-studio cli probe    in.mp4
    python3 -m video-studio cli analyze  in.mp4
    python3 -m video-studio cli timeline project.json
    python3 -m video-studio cli snip     project.json 4.2 6.0
    python3 -m video-studio cli tighten  project.json
    python3 -m video-studio cli caption  project.json "text" 5.0 9.0
    python3 -m video-studio cli undo     project.json
    python3 -m video-studio cli render   project.json out.mp4
    python3 -m video-studio cli verify   out.mp4
    python3 -m video-studio cli --version
"""
import json
import os
import sys

from . import edl


def version_line() -> str:
    """`v1.0.0-dev.N · 2026-11-09 · 19f7a2c` — read from the build, never typed in.

    Same module the header chip and /api/version read, so the line a user copies into
    a bug report is the identity of the code that answered.
    """
    from .version_stamp import as_dict
    info = as_dict()
    parts = ["v" + info["version"]]
    if info["commit_date"]:
        parts.append(info["commit_date"][:10])
    if info["commit"]:
        parts.append(info["commit"][:8])
    return " · ".join(parts) + (" · modified" if info["dirty"] else "")


def _p(obj):
    print(json.dumps(obj, indent=2))


def _project(path):
    try:
        return edl.Project.load(path)
    except FileNotFoundError:
        raise SystemExit(f"no project at {path} — create one with: new <source> {path}")


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]

    if cmd == "probe":
        _p(edl.probe(rest[0]))
    elif cmd == "analyze":
        _p(edl.analyze(rest[0]))
    elif cmd == "verify":
        r = edl.Project(rest[0]).verify(rest[0])
        _p(r)
    elif cmd == "new":
        p = edl.Project(rest[0])
        p.save(rest[1])
        print(f"created {rest[1]} from {p.source}")
    elif cmd == "timeline":
        tl, caps = _project(rest[0]).timeline()
        _p({"segments": tl,
            "total": round(sum(s["duration"] for s in tl), 3),
            "captions": caps})
    elif cmd == "snip":
        p = _project(rest[0])
        p.snip(float(rest[1]), float(rest[2]))
        p.save(rest[0])
        tl, _ = p.timeline()
        print(f"snipped {rest[1]}-{rest[2]} -> {sum(s['duration'] for s in tl):.2f}s")
    elif cmd == "tighten":
        p = _project(rest[0])
        a = edl.analyze(p.source)
        cuts = a["suggested_tighten"]
        if not cuts:
            print("no silences worth removing")
            return 0
        p.snip_many(cuts)
        p.save(rest[0])
        tl, _ = p.timeline()
        print(f"tightened {len(cuts)} pauses -> {sum(s['duration'] for s in tl):.2f}s"
              f" (was {a['duration']:.2f}s)")
    elif cmd == "caption":
        p = _project(rest[0])
        p.caption(rest[1], float(rest[2]), float(rest[3]))
        p.save(rest[0])
        print("caption added")
    elif cmd == "undo":
        p = _project(rest[0])
        gone = p.undo()
        p.save(rest[0])
        tl, _ = p.timeline()
        print(f"undid {gone} -> {sum(s['duration'] for s in tl):.2f}s")
    elif cmd == "render":
        p = _project(rest[0])
        r = p.render(rest[1])
        _p({"rendered": os.path.abspath(rest[1]), "duration": r["duration"],
            "shippable": r["pass"], "failures": r["failures"], "checks": r["checks"]})
    elif cmd in ("version", "--version", "-V"):
        print(version_line())
    else:
        print(f"unknown command {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
