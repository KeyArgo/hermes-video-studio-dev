"""Spec tests for card operations in video_studio.edl (feature not yet implemented).

Run with:  python tests/test_card_ops.py
These tests are written to spec and are EXPECTED to fail until the feature lands.
"""

import os
import subprocess
import sys
import tempfile
import traceback

# Make sure the repo root is importable when run as a script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from video_studio import edl  # noqa: E402
from video_studio.cards import render_card  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_source_mp4(path):
    """6s testsrc2 640x360 30fps + sine audio."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        path,
    ]
    subprocess.run(cmd, check=True)


def _make_card_png(path):
    """1920x1080 PNG via the cards module."""
    render_card(path, title="Test Card", subtitle="spec fixture")


def _probe_duration(path):
    """Return media duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


# ---------------------------------------------------------------------------
# Tiny runner
# ---------------------------------------------------------------------------

_RESULTS = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        _RESULTS.append((name, False, str(e) or "assertion failed"))
    except Exception:
        _RESULTS.append((name, False, traceback.format_exc()))
    else:
        _RESULTS.append((name, True, ""))


def _segments_of_kind(segments, kind):
    return [s for s in segments if s.get("kind") == kind]


def _total_duration(segments):
    return sum(float(s.get("duration", 0.0)) for s in segments)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def main():
    tmp = tempfile.mkdtemp(prefix="card_ops_")
    src = os.path.join(tmp, "source.mp4")
    png = os.path.join(tmp, "card.png")
    out_mp4 = os.path.join(tmp, "out.mp4")

    _make_source_mp4(src)
    _make_card_png(png)

    base = edl.Project(src)
    base_segments, _ = base.timeline()
    base_duration = _total_duration(base_segments)

    # 1. Card at 0 adds exactly `seconds` to total duration.
    def t1():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=0, kind="title")
        segs, _ = p.timeline()
        total = _total_duration(segs)
        assert abs(total - (base_duration + 1.2)) < 1e-6, (
            f"expected total {base_duration + 1.2}, got {total}"
        )

    # 2. Card segment appears with kind == "card" and duration == seconds.
    def t2():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=0, kind="title")
        segs, _ = p.timeline()
        cards = _segments_of_kind(segs, "card")
        assert len(cards) == 1, f"expected 1 card segment, got {len(cards)}"
        c = cards[0]
        assert abs(float(c["duration"]) - 1.2) < 1e-6, (
            f"expected card duration 1.2, got {c['duration']}"
        )
        assert "start" in c, "card segment missing 'start'"

    # 3. Cards are atomic: overlapping snip removes the WHOLE card.
    def t3():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=2.0, kind="title")
        segs_before, _ = p.timeline()
        total_before = _total_duration(segs_before)
        assert abs(total_before - (base_duration + 1.2)) < 1e-6, (
            f"precondition failed: total {total_before}"
        )
        # Snip a range that overlaps the card (card spans [2.0, 3.2)).
        p.snip(2.5, 2.7)
        segs_after, _ = p.timeline()
        cards_after = _segments_of_kind(segs_after, "card")
        assert len(cards_after) == 0, (
            f"expected card fully removed, got {len(cards_after)} card segment(s)"
        )
        total_after = _total_duration(segs_after)
        # Whole card removed -> duration returns to the original. The snip's own
        # 0.2s IS the card's 0.2s, so it cannot be removed twice: this lane's
        # first draft expected base - 0.2 and double-counted. Verified by hand.
        expected = base_duration
        assert abs(total_after - expected) < 1e-6, (
            f"expected total {expected} after atomic card removal, got {total_after}"
        )

    # 4. Snip that does not touch the card leaves it intact.
    def t4():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=4.0, kind="title")
        p.snip(0.5, 1.0)  # far from card at [4.0, 5.2)
        segs, _ = p.timeline()
        cards = _segments_of_kind(segs, "card")
        assert len(cards) == 1, f"expected card intact, got {len(cards)}"
        assert abs(float(cards[0]["duration"]) - 1.2) < 1e-6, (
            f"expected card duration 1.2, got {cards[0]['duration']}"
        )

    # 5. at=None appends the card at the end of the timeline.
    def t5():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=None, kind="title")
        segs, _ = p.timeline()
        cards = _segments_of_kind(segs, "card")
        assert len(cards) == 1, f"expected 1 card, got {len(cards)}"
        c = cards[0]
        # The card should start at or after the end of the source content.
        src_segs = _segments_of_kind(segs, "src")
        src_end = max(
            (float(s["start"]) + float(s["duration"]) for s in src_segs),
            default=0.0,
        )
        assert float(c["start"]) >= src_end - 1e-6, (
            f"card start {c['start']} should be >= src end {src_end}"
        )
        # And it should be the last segment.
        last = segs[-1]
        assert last.get("kind") == "card", (
            f"expected last segment to be card, got {last.get('kind')}"
        )

    # 6. render() produces a file within 0.35s of sum of segment durations,
    #    and returns pass == True.
    def t6():
        p = edl.Project(src)
        p.card(png, seconds=1.2, at=0, kind="title")
        segs, _ = p.timeline()
        expected = _total_duration(segs)
        result = p.render(out_mp4)
        assert isinstance(result, dict), f"render() must return dict, got {type(result)}"
        assert "duration" in result, "render() result missing 'duration'"
        assert "pass" in result, "render() result missing 'pass'"
        assert result["pass"] is True, f"render() pass != True: {result['pass']!r}"
        assert os.path.exists(out_mp4), f"render() did not create {out_mp4}"
        actual = _probe_duration(out_mp4)
        assert abs(actual - expected) <= 0.35, (
            f"rendered duration {actual} not within 0.35s of expected {expected}"
        )

    check("1. card at 0 adds exactly seconds to total duration", t1)
    check("2. card segment has kind=='card' and duration==seconds", t2)
    check("3. overlapping snip removes the WHOLE card (atomic)", t3)
    check("4. non-overlapping snip leaves card intact", t4)
    check("5. at=None appends card at end of timeline", t5)
    check("6. render() duration within 0.35s and pass==True", t6)

    failed = 0
    for name, ok, msg in _RESULTS:
        if ok:
            print(f"PASS: {name}")
        else:
            failed += 1
            print(f"FAIL: {name}")
            for line in msg.splitlines():
                print(f"      {line}")

    print(f"\n{len(_RESULTS) - failed}/{len(_RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
