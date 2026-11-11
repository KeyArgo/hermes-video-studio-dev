"""Regression test for the installer loop in video_studio.capture.

install_desktop_recorder queues up to two install attempts (quiet ``sudo -n``
then a ``pkexec`` prompt). The old loop recorded each result by overwriting
``attempts[-1]`` while iterating ``attempts`` — so once two attempts were queued
the second iteration pulled the just-written result dict out of the list and
passed it to ``subprocess.run``, raising ``TypeError`` instead of trying the
prompt. No real process is spawned here: ``subprocess.run`` is stubbed.
"""
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import capture  # noqa: E402


def test_two_attempts_do_not_crash_and_are_both_reported(monkeypatch):
    # both install paths available, recorder still missing afterwards
    monkeypatch.setattr(capture, "deps", lambda: {"desktop": {
        "ready": False, "recorder": None,
        "command": "sudo zypper install wf-recorder",
        "can_install_quietly": True, "can_install_with_prompt": True}})

    calls = []

    def fake_run(cmd, *a, **k):
        # the loop must only ever hand a list of strings to subprocess.run
        assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd), cmd
        calls.append(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="nope")

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    monkeypatch.setattr(shutil, "which", lambda name: None)  # local `import shutil`

    result = capture.install_desktop_recorder(use_prompt=True)

    assert result["ok"] is False
    # both attempts ran (sudo then pkexec) without a TypeError...
    assert len(calls) == 2, calls
    assert calls[0][0] == "sudo" and calls[1][0] == "pkexec"
    # ...and both are reported back
    assert len(result["attempted"]) == 2, result["attempted"]
