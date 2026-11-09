#!/usr/bin/env python3
"""Card renderer checks — plain asserts, no pytest, no Pillow.

    python3 tests/test_cards.py
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from video_studio.cards import render_card  # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f"  PASS  {name:<40} {detail}")
    except Exception as exc:  # noqa: BLE001
        FAIL.append(name)
        print(f"  FAIL  {name:<40} {type(exc).__name__}: {exc}")


def png_size(path):
    """Width and height straight out of the IHDR chunk — no dependencies."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def main():
    tmp = tempfile.mkdtemp(prefix="hvs-cards-")
    spec = dict(title="Omniscience", subtitle="agent flight recorder",
                claim="Every model call, tool call and failure, recorded.",
                install="hermes plugins install omniscience",
                limit="It records metadata, not prompts.")

    def t_all_kinds():
        out = []
        for kind in ("title", "limit", "end"):
            p = os.path.join(tmp, f"{kind}.png")
            render_card(p, kind, **spec)
            assert os.path.exists(p), kind
            assert os.path.getsize(p) > 8000, f"{kind}: {os.path.getsize(p)} bytes"
            out.append(f"{kind}={os.path.getsize(p)//1024}KB")
        return " ".join(out)

    def t_geometry():
        p = os.path.join(tmp, "geo.png")
        render_card(p, "title", **spec)
        w, h = png_size(p)
        assert (w, h) == (1920, 1080), (w, h)
        return f"{w}x{h}"

    def t_bad_kind():
        try:
            render_card(os.path.join(tmp, "x.png"), "nope", title="x")
        except ValueError:
            return "ValueError raised"
        raise AssertionError("unknown kind was accepted")

    def t_escaping():
        p = os.path.join(tmp, "esc.png")
        render_card(p, "limit", title="A <b> & B", limit='quotes " and <script>alert(1)</script>')
        assert os.path.getsize(p) > 8000
        return "markup escaped, still rendered"

    print("\nVideo Studio — card renderer, 4 checks\n")
    for name, fn in [("renders title/limit/end", t_all_kinds),
                     ("1920x1080 exactly", t_geometry),
                     ("rejects unknown kind", t_bad_kind),
                     ("escapes HTML in text", t_escaping)]:
        check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
