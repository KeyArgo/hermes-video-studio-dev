"""Regression test for output-directory creation in video_studio.drafts.assemble.

ffmpeg writes its concatenated draft straight to ``dest``; its parent directory
must therefore exist before ffmpeg runs. The old code called
``dest.parent.mkdir(...)`` only *after* a successful run, so assembling into a
not-yet-existing folder failed (and the mkdir could never run). ffmpeg is stubbed
here — the test checks that the directory exists at the moment ffmpeg is invoked.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import drafts  # noqa: E402


def test_output_directory_exists_before_ffmpeg_runs(tmp_path, monkeypatch):
    dest = tmp_path / "renders" / "drafts" / "out.mp4"   # parent does not exist yet
    assert not dest.parent.exists()
    seen = {}

    def fake_run(args, *a, **k):
        seen["parent_exists"] = dest.parent.is_dir()
        dest.write_bytes(b"\x00\x00")  # stand in for ffmpeg's output
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(drafts.subprocess, "run", fake_run)

    result = drafts.assemble([tmp_path / "a.mp4"], dest)

    assert seen["parent_exists"] is True, "assemble must create dest dir before ffmpeg writes"
    assert result["output"] == str(dest)
    assert result["bytes"] == 2
