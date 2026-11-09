#!/usr/bin/env python3
"""Every claim in the README as a check. No pytest needed: python3 tests/run_tests.py

    python3 tests/run_tests.py          # builds an 8s fixture, runs 9 checks
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from video_studio import edl  # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append((name, detail))
        print(f"  PASS  {name:<46} {detail}")
    except Exception as exc:  # noqa: BLE001
        FAIL.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {name:<46} {type(exc).__name__}: {exc}")


def fixture(dirpath):
    """8s: testing pattern + a tone gated to leave repeating ~1.4s silences."""
    out = os.path.join(dirpath, "fixture.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
        "-af", "volume=0:enable='gte(mod(t,3),1.6)'",
        "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", out], check=True)
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="hewncut-tests-")
    src = fixture(tmp)

    def t_probe():
        i = edl.probe(src)
        assert abs(i["duration"] - 8.0) < 0.3, i["duration"]
        assert (i["width"], i["height"]) == (640, 360)
        return f'{i["duration"]}s {i["width"]}x{i["height"]}'

    def t_analyze():
        a = edl.analyze(src)
        assert a["suggested_tighten"], "expected suggested cuts in gated audio"
        assert a["head_silence_seconds"] == 0 or a["head_silence_seconds"] >= 0
        return f'{len(a["suggested_tighten"])} cut(s), {a["would_remove_seconds"]}s'

    def t_analyze_protects_cards():
        a = edl.analyze(src)
        for start, end in a["suggested_tighten"]:
            assert start > 0.05 and end < a["duration"] - 0.05, (start, end)
        return "head/tail never proposed"

    def t_snip_exact():
        p = edl.Project(src)
        p.snip(2.0, 3.0)
        tl, _ = p.timeline()
        total = sum(s["duration"] for s in tl)
        assert abs(total - 7.0) < 0.05, total
        assert len(tl) == 2, len(tl)
        return f"{total:.2f}s in {len(tl)} segments"

    def t_undo_restores():
        p = edl.Project(src)
        p.snip(1.0, 2.0)
        p.snip(3.0, 4.0)
        p.undo()
        tl, _ = p.timeline()
        assert abs(sum(s["duration"] for s in tl) - 7.0) < 0.05
        p.undo()
        tl, _ = p.timeline()
        assert abs(sum(s["duration"] for s in tl) - 8.0) < 0.1
        return "undo pops one op at a time"

    def t_speed_retimes():
        p = edl.Project(src)
        p.speed(2.0, 0.0, 4.0)
        tl, _ = p.timeline()
        assert abs(sum(s["duration"] for s in tl) - 6.0) < 0.1, \
            sum(s["duration"] for s in tl)
        return "4s at 2x = 2s"

    def t_caption_limit():
        p = edl.Project(src)
        try:
            p.caption("one two three four five six seven eight", 0, 2)
        except ValueError:
            return "8 words rejected"
        raise AssertionError("caption accepted 8 words")

    def t_verify_catches_length():
        p = edl.Project(src)
        r = p.verify(src, max_seconds=5.0)
        assert not r["pass"], "8s file passed a 5s cap"
        assert "duration <= cap" in r["failures"], r["failures"]
        return "8s fails a 5s cap"

    def t_render_roundtrip():
        p = edl.Project(src)
        p.snip(6.0, 8.0)
        p.caption("one clip, one cut", 0.4, 2.4)
        out = os.path.join(tmp, "rendered.mp4")
        r = p.render(out)
        assert os.path.exists(out) and os.path.getsize(out) > 10_000
        assert abs(r["duration"] - 6.0) < 0.35, r["duration"]
        assert r["pass"], r["failures"]
        return f'{r["duration"]:.2f}s, shippable={r["pass"]}'

    def t_tighten_uses_timeline_space():
        p = edl.Project(src)
        p.speed(2.0, 0.0, 4.0)                 # timeline is now 6s, source is 8s
        a = p.analyze_timeline()
        assert a["space"] == "timeline"
        assert abs(a["duration"] - 6.0) < 0.15, a["duration"]
        for start, end in a["suggested_tighten"]:
            assert end <= a["duration"] + 0.05, (start, end, a["duration"])
        return f'{len(a["suggested_tighten"])} cut(s) inside the 6s timeline'

    def t_caption_follows_cut():
        # regression: captions used to stay put when an earlier snip moved the
        # timeline, so a cut silently slid caption text off its footage
        p = edl.Project(src)
        p.caption("six words at the end", 4.0, 7.0)
        p.snip(1.0, 3.0)                       # everything after 1.0 moves left 2s
        _, caps = p.timeline()
        assert len(caps) == 1, caps
        assert abs(caps[0]["start"] - 2.0) < 0.02 and abs(caps[0]["end"] - 5.0) < 0.02, caps
        return f'caption moved to {caps[0]["start"]:.1f}-{caps[0]["end"]:.1f}'

    def t_caption_survives_speed():
        p = edl.Project(src)
        p.caption("after the fast bit", 5.0, 7.0)
        p.speed(2.0, 0.0, 4.0)                 # 4s -> 2s, so 2s are gone
        _, caps = p.timeline()
        assert len(caps) == 1, caps
        assert abs(caps[0]["start"] - 3.0) < 0.02 and abs(caps[0]["end"] - 5.0) < 0.02, caps
        return f'caption moved to {caps[0]["start"]:.1f}-{caps[0]["end"]:.1f}'

    def t_drop_middle_op():
        # revision: remove one edit from the middle, keep the ones after it
        p = edl.Project(src)
        p.snip(1.0, 2.0)          # op 0
        p.snip(4.0, 5.0)          # op 1  (timeline coords after op 0)
        p.speed(2.0, 0.0, 3.0)    # op 2
        before = sum(s["duration"] for s in p.timeline()[0])
        gone = p.drop(1)
        after = sum(s["duration"] for s in p.timeline()[0])
        assert gone["op"] == "snip"
        assert abs(after - (before + 1.0)) < 0.05, (before, after)
        assert len(p.ops) == 2
        return f"dropped op 1, {before:.2f}s -> {after:.2f}s"

    def t_edit_caption():
        p = edl.Project(src)
        p.caption("first wording here", 4.0, 6.0)
        p.edit_op(0, text="second wording")
        _, caps = p.timeline()
        assert caps[0]["text"] == "second wording", caps
        try:
            p.edit_op(0, text="one two three four five six seven eight")
        except ValueError:
            return "text replaced, 8 words still rejected"
        raise AssertionError("edit accepted 8 words")

    def t_relink_guards_duration():
        p = edl.Project(src)
        other = os.path.join(tmp, "other.mp4")
        import subprocess as sp
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=30:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", other], check=True)
        try:
            p.relink(other)
        except ValueError:
            return "3s file refused for an 8s cut"
        raise AssertionError("relink accepted a different length")

    print("\nVideo Studio — 15 checks\n")
    for name, fn in [("probe reports facts", t_probe),
                     ("analyze proposes cuts", t_analyze),
                     ("analyze protects title/end", t_analyze_protects_cards),
                     ("snip is frame-accurate", t_snip_exact),
                     ("undo pops one op", t_undo_restores),
                     ("speed retimes the timeline", t_speed_retimes),
                     ("caption enforces 7 words", t_caption_limit),
                     ("verify catches a long cut", t_verify_catches_length),
                     ("render + gate round-trip", t_render_roundtrip),
                     ("tighten measures the timeline", t_tighten_uses_timeline_space),
                     ("captions follow a snip", t_caption_follows_cut),
                     ("captions follow speed", t_caption_survives_speed),
                     ("drop one op from the middle", t_drop_middle_op),
                     ("edit a caption in place", t_edit_caption),
                     ("relink refuses a different length", t_relink_guards_duration)]:
        check(name, fn)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
