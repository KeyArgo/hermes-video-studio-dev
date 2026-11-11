"""Regression tests for the fast-preview filtergraph in video_studio.service.

service.render(fast=True) builds a 720p preview filtergraph. Two defects lived in
the old inline builder; both are asserted here against the extracted pure helper
``_preview_filtergraph`` (no ffmpeg runs, so this guards CI too):

* a silent source emitted ``[a{i}]`` audio labels and ``concat ...:a=1`` even
  though no ``[0:a]`` audio pad was ever defined — the preview failed outright;
* a card segment was trimmed from the source stream ``[0:v]`` instead of its own
  PNG input, so the preview showed footage where the card belongs.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import service  # noqa: E402


def _src(src_in, src_out, dur, speed=1.0, muted=False):
    return {"src_in": src_in, "src_out": src_out, "duration": dur,
            "speed": speed, "muted": muted, "kind": "src"}


def _card(dur, png):
    return {"src_in": 0.0, "src_out": dur, "duration": dur,
            "speed": 1.0, "muted": True, "kind": "card", "png": png}


def test_silent_source_builds_a_valid_audioless_concat():
    tl = [_src(0.0, 5.0, 5.0)]
    graph, extra_inputs, maps = service._preview_filtergraph(tl, has_audio=False)
    joined = ";".join(graph)
    # no audio pad is referenced that was never produced
    assert "[a0]" not in joined, joined
    assert "[0:a]" not in joined, joined
    # concat is audio-less and a synthetic track is supplied for the [ac] map
    assert "concat=n=1:v=1:a=0[vc]" in joined, joined
    assert "anullsrc" in joined and "[ac]" in joined, joined
    assert maps == ["-map", "[vc]", "-map", "[ac]"]


def test_audio_source_preview_is_unchanged_for_the_common_case():
    tl = [_src(0.0, 5.0, 5.0), _src(5.0, 8.0, 3.0)]
    graph, extra_inputs, maps = service._preview_filtergraph(tl, has_audio=True)
    joined = ";".join(graph)
    assert extra_inputs == []
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]" in joined, joined
    # each source segment draws its own picture and sound from input 0
    assert joined.count("[0:v]trim") == 2
    assert joined.count("[0:a]atrim") == 2


def test_card_segment_uses_its_own_png_input_not_source_video():
    tl = [_src(0.0, 4.0, 4.0), _card(1.2, "/tmp/cards/title.png")]
    graph, extra_inputs, maps = service._preview_filtergraph(tl, has_audio=True)
    joined = ";".join(graph)
    # the card PNG is added as a looped input...
    assert extra_inputs == ["-loop", "1", "-i", "/tmp/cards/title.png"], extra_inputs
    # ...and the card segment's video comes from input 1, not a [0:v] source trim
    assert "[1:v]scale=" in joined, joined
    # exactly one real source-video trim (segment 0), the card does not add another
    assert joined.count("[0:v]trim") == 1, joined
    # the card gets a silent audio pad so concat a=1 stays balanced
    assert "anullsrc" in joined and "[a1]" in joined, joined
