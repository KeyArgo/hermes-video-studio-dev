"""Regression tests for caption re-timing in video_studio.edl.transform_captions.

transform_captions is a pure function: given captions in timeline seconds BEFORE an
op and the op's parameters, it returns the captions in timeline seconds AFTER the op.
No ffmpeg is involved, so these checks are fast and deterministic.

The focus here is the ``speed`` op with a factor other than 2.0. Every existing
check in the repo uses a 2x speed, and at exactly 2x two different formulas for the
"after the sped-up region" offset happen to agree — so a wrong formula hid behind
the tests. These cases exercise factor 3 (and 4), where a caption that spans or
reaches past a sped-up region must keep its trailing piece at the correct moment
instead of sliding away or vanishing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import edl  # noqa: E402


def _caps(*triples):
    return [{"start": s, "end": e, "text": t} for s, e, t in triples]


def _near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# speed op — the regression
# --------------------------------------------------------------------------- #
def test_speed_caption_after_region_shifts_by_the_saved_time():
    # region [2,5], factor 3 -> 3s of footage becomes 1s, saving shift = 2s.
    # a caption entirely after the region moves left by exactly that saving.
    out = edl.transform_captions(_caps((6.0, 7.0, "after")), "speed", (2.0, 5.0), 3.0)
    assert len(out) == 1
    assert _near(out[0]["start"], 4.0) and _near(out[0]["end"], 5.0)


def test_speed_caption_spanning_region_keeps_its_trailing_piece():
    # caption [1,6] covers before, during and after the sped region [2,5] (f=3).
    # The part after the region (old [5,6]) must land at [5-shift, 6-shift] = [3,4],
    # NOT collapse to zero width (the old bug) or slide to the wrong time.
    out = edl.transform_captions(_caps((1.0, 6.0, "span")), "speed", (2.0, 5.0), 3.0)
    starts = sorted((round(c["start"], 6), round(c["end"], 6)) for c in out)
    # before-piece: [1,2]
    assert (1.0, 2.0) in starts, starts
    # after-piece must survive at [3,4]
    assert any(_near(s, 3.0) and _near(e, 4.0) for s, e in starts), starts


def test_speed_caption_starting_inside_ending_after():
    # caption [3,6]: starts inside the sped region, ends after it (f=3).
    # new start = 2 + (3-2)/3 = 2.3333; region ends at 2 + 3/3 = 3.0;
    # trailing piece (old [5,6]) -> [3,4].
    out = edl.transform_captions(_caps((3.0, 6.0, "inside_out")), "speed", (2.0, 5.0), 3.0)
    pieces = sorted((round(c["start"], 6), round(c["end"], 6)) for c in out)
    assert any(_near(s, 2.0 + 1.0 / 3.0) and _near(e, 3.0) for s, e in pieces), pieces
    assert any(_near(s, 3.0) and _near(e, 4.0) for s, e in pieces), pieces


def test_speed_caption_trailing_piece_no_gap_at_factor_four():
    # region [0,4], factor 4 -> becomes 1s, shift = 3s. caption [1,6] starts inside,
    # region ends at 0 + 4/4 = 1.0; trailing (old [4,6]) -> [1,3].
    out = edl.transform_captions(_caps((1.0, 6.0, "x")), "speed", (0.0, 4.0), 4.0)
    pieces = sorted((round(c["start"], 6), round(c["end"], 6)) for c in out)
    # the piece inside the sped region ends at 1.0, the trailing piece begins at 1.0:
    # contiguous, no gap, no overlap.
    assert any(_near(e, 1.0) for s, e in pieces), pieces
    assert any(_near(s, 1.0) and _near(e, 3.0) for s, e in pieces), pieces


def test_speed_factor_two_still_correct():
    # guards the fix: the common 2x path must be unchanged.
    # region [0,4] f=2 -> 2s, shift=2. caption [5,7] (after) -> [3,5].
    out = edl.transform_captions(_caps((5.0, 7.0, "after")), "speed", (0.0, 4.0), 2.0)
    assert len(out) == 1
    assert _near(out[0]["start"], 3.0) and _near(out[0]["end"], 5.0)


# --------------------------------------------------------------------------- #
# snip op — invariants that must keep holding after the fix
# --------------------------------------------------------------------------- #
def test_snip_caption_spanning_removed_range_is_contiguous():
    # remove [2,4]; caption [1,6] -> material between 2 and 4 is gone, so the
    # caption covers [1,4] with no gap (emitted as adjacent pieces [1,2]+[2,4]).
    out = edl.transform_captions(_caps((1.0, 6.0, "s")), "snip", (2.0, 4.0))
    pieces = sorted((round(c["start"], 6), round(c["end"], 6)) for c in out)
    assert _near(pieces[0][0], 1.0), pieces          # starts where it started
    assert _near(pieces[-1][1], 4.0), pieces          # ends 2s earlier (range removed)
    # contiguous: each piece begins where the previous ended
    for (s0, e0), (s1, e1) in zip(pieces, pieces[1:]):
        assert _near(e0, s1), pieces


def test_snip_caption_after_range_shifts_left():
    out = edl.transform_captions(_caps((5.0, 6.0, "s")), "snip", (1.0, 3.0))
    assert len(out) == 1
    assert _near(out[0]["start"], 3.0) and _near(out[0]["end"], 4.0)
