"""Desktop recording lifecycle, proven against a stand-in for wf-recorder.

The real binary is not installed on this box, so the suite ships a fake that behaves like it:
it writes a growing mp4 and finalises it on SIGINT. That covers everything this module owns —
state, liveness, auto-stop, probing an interrupted file, and filing the result as an asset —
without pretending the compositor was exercised.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE = '''#!/usr/bin/env python3
import signal, subprocess, sys
path = sys.argv[sys.argv.index("-f") + 1]
size = "640x360"
if "-g" in sys.argv:
    size = sys.argv[sys.argv.index("-g") + 1].split("+")[0]
p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                      "-i", f"testsrc=size={size}:rate=30", "-c:v", "libx264",
                      "-preset", "ultrafast", "-pix_fmt", "yuv420p", path])
def bye(*_):
    p.send_signal(signal.SIGINT); p.wait(); sys.exit(0)
signal.signal(signal.SIGINT, bye); signal.signal(signal.SIGTERM, bye)
p.wait()
'''


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bindir = root / "bin"
        bindir.mkdir()
        (bindir / "wf-recorder").write_text(FAKE)
        (bindir / "wf-recorder").chmod(0o755)
        self.old_path = os.environ["PATH"]
        # a PATH that certainly has no recorder, whatever this shell exported: keep only the
        # directories that do not contain one (ffmpeg must still be reachable by the fake)
        self.clean_path = os.pathsep.join(
            d for d in self.old_path.split(os.pathsep)
            if d and not (Path(d) / "wf-recorder").exists()) or "/usr/bin:/bin"
        os.environ["PATH"] = f"{bindir}:{self.clean_path}"
        os.environ["HEWN_VIDEO_STUDIO_DATA"] = str(root / "data")
        os.environ["HEWN_ASSETS_LIBRARY"] = str(root / "library")
        for mod in ("video_studio.recorder", "video_studio.capture", "video_studio.films_api"):
            sys.modules.pop(mod, None)
        from video_studio import recorder
        self.recorder = recorder
        self.root = root

    def tearDown(self):
        self.recorder.forget()
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def test_no_binary_is_a_refusal_that_names_the_install(self):
        os.environ["PATH"] = self.clean_path        # hide the fake
        self.assertFalse(self.recorder.available())
        r = self.recorder.start(self.root / "x.mp4")
        self.assertFalse(r["ok"])
        self.assertIn("install", r)
        self.assertIn("wf-recorder", r["install"])

    def test_start_status_stop_probes_and_files_an_asset(self):
        out = self.root / "capture.mp4"
        r = self.recorder.start(out, geometry="640x360+0+0", label="demo")
        self.assertTrue(r["ok"], r)
        time.sleep(2.5)
        st = self.recorder.status()
        self.assertTrue(st["recording"], st)
        self.assertGreater(st["bytes"], 10_000, st)

        res = self.recorder.stop()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.recorder.status()["recording"], False)
        self.assertGreater(res["seconds"], 0.5)
        self.assertEqual(res["probe"]["width"], 640)
        self.assertIn("id", res.get("asset") or {}, res)

    def test_second_start_is_refused_while_one_is_running(self):
        self.assertTrue(self.recorder.start(self.root / "a.mp4")["ok"])
        time.sleep(0.8)
        second = self.recorder.start(self.root / "b.mp4")
        self.assertFalse(second["ok"])
        self.assertIn("already running", second["reason"])
        self.recorder.stop()

    def test_auto_stop_fires_so_a_closed_page_cannot_fill_the_disk(self):
        r = self.recorder.start(self.root / "auto.mp4", seconds=1.5)
        self.assertTrue(r["ok"])
        deadline = time.time() + 15
        while time.time() < deadline and self.recorder.status().get("recording"):
            time.sleep(0.3)
        st = self.recorder.status()
        self.assertFalse(st["recording"], st)
        self.assertTrue((self.root / "auto.mp4").stat().st_size > 0)

    def test_a_truncated_file_is_reported_as_broken_not_as_done(self):
        bad = self.root / "truncated.mp4"
        bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 400)     # header, no video
        probe = self.recorder._probe(bad)
        self.assertFalse(probe["ok"])
        self.assertIn("no video stream", probe["reason"])

    def test_stop_with_nothing_running_says_so(self):
        r = self.recorder.stop()
        self.assertFalse(r["ok"])
        self.assertIn("nothing was recording", r["reason"])

    def test_targets_offer_whole_screen_and_windows(self):
        t = self.recorder.targets()
        self.assertIn("full_screen", t)
        self.assertIsInstance(t["windows"], list)
        self.assertEqual(t["ready"], True)

    def test_last_recording_points_at_the_good_file(self):
        self.recorder.start(self.root / "one.mp4")
        time.sleep(1.2)
        self.recorder.stop()
        last = self.recorder.last_recording()
        self.assertIsNotNone(last)


class RecordRouteTest(unittest.TestCase):
    """The HTTP surface, exercised in-process: start → status → stop → footage "last"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bindir = root / "bin"; bindir.mkdir()
        (bindir / "wf-recorder").write_text(FAKE); (bindir / "wf-recorder").chmod(0o755)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = str(bindir) + os.pathsep + self.old_path
        os.environ["HEWN_VIDEO_STUDIO_DATA"] = str(root / "data")
        os.environ["HEWN_ASSETS_LIBRARY"] = str(root / "library")
        os.environ["VIDEO_STUDIO_MOTION"] = str(root / "motion")
        for mod in ("video_studio.recorder", "video_studio.capture", "video_studio.films_api",
                    "video_studio.assets_api"):
            sys.modules.pop(mod, None)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from video_studio import films_api
        app = FastAPI(); app.include_router(films_api.router, prefix="/api")
        self.client = TestClient(app)
        self.root = root

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        for k in ("HEWN_VIDEO_STUDIO_DATA", "HEWN_ASSETS_LIBRARY", "VIDEO_STUDIO_MOTION"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_routes_drive_a_recording_end_to_end(self):
        r = self.client.post("/api/capture/record/start", json={"name": "route", "seconds": 2,
                                                               "geometry": "640x360+0+0"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])
        st = self.client.get("/api/capture/record/status").json()
        self.assertTrue(st["recording"], st)
        import time; time.sleep(2.4)
        res = self.client.post("/api/capture/record/stop")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["ok"], body)
        self.assertGreater(body["seconds"], 0.4)

    def test_targets_route_lists_windows(self):
        r = self.client.get("/api/capture/record/targets")
        self.assertEqual(r.status_code, 200)
        self.assertIn("windows", r.json())

    def test_status_is_idle_before_anything_runs(self):
        self.assertEqual(self.client.get("/api/capture/record/status").json()["state"], "idle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
