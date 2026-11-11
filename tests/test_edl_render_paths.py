"""Regression tests for ffmpeg filtergraph path escaping in video_studio.edl.

The caption pass in ``Project.render`` writes an ASS subtitle file into a temp
directory and burns it in with ``-vf ass=<path>``. ffmpeg's filtergraph parser
treats ``:`` as the option separator and ``\\`` as an escape character, so a raw
Windows path (``C:\\Users\\me\\...\\caps.ass``) is mis-parsed: the drive-letter
colon splits the filter options and the backslashes are swallowed. The result is
a failed (or wrong) caption pass.

These tests assert on the constructed filter value only — no ffmpeg is run — so
they guard the escaping on CI (Linux) too.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import edl  # noqa: E402


def test_windows_drive_colon_is_escaped():
    raw = r"C:\Users\me\AppData\Local\Temp\hewncut-abc\caps.ass"
    esc = edl._filter_path(raw)
    # separators normalised to forward slashes (ffmpeg accepts them on Windows)
    assert "\\Users" not in esc and "/Users/" in esc
    # the drive-letter colon must be escaped so it does not split filter options
    assert esc.startswith("C\\:/")
    # no bare, unescaped colon survives
    assert ":" not in esc.replace("\\:", "")


def test_built_vf_arg_is_single_literal_value():
    raw = r"C:\tmp\hewncut-x\caps.ass"
    vf = f"ass={edl._filter_path(raw)}"
    # the only colon left is the escaped drive-letter one; the filter name/value
    # separator (=) is intact and the value is not split by a stray ':'
    assert vf == r"ass=C\:/tmp/hewncut-x/caps.ass"


def test_posix_path_is_unchanged():
    raw = "/tmp/hewncut-abc/caps.ass"
    assert edl._filter_path(raw) == raw


def test_filtergraph_metacharacters_in_path_are_escaped():
    # a temp dir could in principle contain filtergraph metacharacters; they must
    # not be able to terminate the value or inject another filter option.
    raw = "/tmp/od[d],dir/caps.ass"
    esc = edl._filter_path(raw)
    assert "\\[" in esc and "\\]" in esc and "\\," in esc
