"""A render that outlives the window is the bug: prove the process is visible, stoppable, and reaped.

These run real processes (`sleep`) rather than mocks, because the thing under test is process
lifetime — a mock would pass while the real child kept burning a core.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HEWN_VIDEO_STUDIO_DATA"] = self.tmp.name
        os.environ["VIDEO_STUDIO_RENDER_NICE"] = "10"
        for mod in ("video_studio.jobs",):
            sys.modules.pop(mod, None)
        from video_studio import jobs
        self.jobs = jobs

    def tearDown(self):
        try:
            self.jobs.reap_all(wait=2.0)
        finally:
            os.environ.pop("HEWN_VIDEO_STUDIO_DATA", None)
            os.environ.pop("VIDEO_STUDIO_RENDER_NICE", None)
            self.tmp.cleanup()

    def test_a_running_job_is_visible_with_its_pid(self):
        job = self.jobs.start("test", ["sleep", "30"], film="f", label="sleep")
        time.sleep(0.4)
        rows = self.jobs.list_active()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["pid"], job["pid"])
        self.assertTrue(rows[0]["alive"])
        self.assertEqual(rows[0]["state"], "running")

    def test_cancel_actually_stops_the_process(self):
        job = self.jobs.start("test", ["sleep", "30"])
        time.sleep(0.4)
        out = self.jobs.cancel(job["id"])
        self.assertTrue(out["ok"], out)
        time.sleep(0.3)
        self.assertEqual(self.jobs.list_active()[0]["alive"], False)

    def test_renders_run_at_lower_priority(self):
        job = self.jobs.start("test", ["sleep", "5"])
        self.assertIn("nice", " ".join(job["cmd"]))
        self.assertEqual(self.jobs.list_active()[0]["nice"], 10)

    def test_reap_all_is_what_shutdown_calls(self):
        a = self.jobs.start("test", ["sleep", "30"])
        b = self.jobs.start("test", ["sleep", "30"])
        time.sleep(0.4)
        out = self.jobs.reap_all(wait=2.0)
        self.assertEqual(sorted(out["stopped"]), sorted([a["id"], b["id"]]), out)
        self.assertEqual([r for r in self.jobs.list_active() if r["alive"]], [])

    def test_a_stale_entry_is_reported_orphaned_not_silently_forgotten(self):
        # write through the module's own path: a stale registry is what a host restart leaves
        self.jobs.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        self.jobs.REGISTRY.write_text(json.dumps(
            {"old-1": {"id": "old-1", "kind": "assemble", "pid": 999999,
                       "started": time.time() - 60, "state": "running"}}))
        rows = self.jobs.list_active()
        self.assertEqual(rows[0]["state"], "orphaned")

    def test_idle_report_states_the_policy_and_the_active_count(self):
        rep = self.jobs.idle_report()
        self.assertEqual(rep["active_jobs"], 0)
        self.assertEqual(rep["policy"]["nice"], 10)

    def test_cancel_of_unknown_job_is_a_clean_refusal(self):
        out = self.jobs.cancel("nope")
        self.assertFalse(out["ok"])
        self.assertIn("no job", out["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
