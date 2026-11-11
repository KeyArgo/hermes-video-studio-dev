"""Regression tests for caption re-timing when a card is inserted mid-timeline.

A card op wedges `seconds` of new material into the timeline at position `at`,
pushing every later segment to the right by that duration. Captions live in
timeline seconds, so a caption after `at` must move with its footage — otherwise
it drifts onto earlier material (the same class of bug already fixed for
snip/trim/speed). These exercise ``transform_captions(..., "insert", ...)``,
which is pure (no ffmpeg), so they guard the fix on CI too.
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


def test_caption_after_insertion_shifts_right_by_card_duration():
    # insert a 1.2s card at t=2.0; a caption at [4,5] must land at [5.2,6.2].
    out = edl.transform_captions(_caps((4.0, 5.0, "after")), "insert", (2.0, 3.2))
    assert len(out) == 1
    assert _near(out[0]["start"], 5.2) and _near(out[0]["end"], 6.2)


def test_caption_before_insertion_is_unchanged():
    out = edl.transform_captions(_caps((0.5, 1.5, "before")), "insert", (2.0, 3.2))
    assert len(out) == 1
    assert _near(out[0]["start"], 0.5) and _near(out[0]["end"], 1.5)


def test_caption_at_insertion_point_shifts():
    # a caption starting exactly at `at` annotates material that moves right.
    out = edl.transform_captions(_caps((2.0, 3.0, "at")), "insert", (2.0, 3.2))
    assert len(out) == 1
    assert _near(out[0]["start"], 3.2) and _near(out[0]["end"], 4.2)


def test_caption_straddling_insertion_splits_and_keeps_tail():
    # caption [1,4] straddles an insert at 2.0 (card len 1.2): head [1,2] stays,
    # tail (old [2,4]) moves to [3.2,5.2]. No material is lost or overlapped.
    out = edl.transform_captions(_caps((1.0, 4.0, "span")), "insert", (2.0, 3.2))
    pieces = sorted((round(c["start"], 6), round(c["end"], 6)) for c in out)
    assert any(_near(s, 1.0) and _near(e, 2.0) for s, e in pieces), pieces
    assert any(_near(s, 3.2) and _near(e, 5.2) for s, e in pieces), pieces


def test_unknown_kind_still_returns_empty():
    # guardrail: the "insert" branch must not have changed the fall-through.
    assert edl.transform_captions(_caps((1.0, 2.0, "x")), "bogus") == []
