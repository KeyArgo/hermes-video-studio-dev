"""The review path: measurements the model is given, and the model's answer when it is messy.

No network here — the model call itself is proven by a real run against the DeepSeek lane, which
is recorded in the handover. What is tested is everything that can silently rot: the measurements,
the JSON extraction from a chatty reply, and the model registry.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_studio import review


def clip(path: Path, seconds: float = 3.0, pattern: str = "testsrc") -> Path:
    """A source clip. `color` needs its own option names (c/s/r/d), testsrc uses long ones."""
    src = (f"color=c=black:s=320x180:r=30:d={seconds}" if pattern.startswith("color")
           else f"{pattern}=size=320x180:rate=30:duration={seconds}")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class MeasurementTest(unittest.TestCase):
    def test_motion_profile_is_per_second_and_positive_on_moving_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = clip(Path(tmp) / "a.mp4", 3.0)
            prof = review.motion_profile(p)
            self.assertEqual(len(prof), 3)
            self.assertTrue(all(v > 0 for v in prof), prof)

    def test_a_still_clip_reports_no_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = clip(Path(tmp) / "s.mp4", 2.0, "color=c=black")
            self.assertEqual(review.motion_profile(p), [0.0, 0.0])

    def test_frame_stats_separate_black_from_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            dark = clip(Path(tmp) / "d.mp4", 1.0, "color=c=black")
            busy = clip(Path(tmp) / "b.mp4", 1.0, "testsrc")
            s_dark, s_busy = review.frame_stats(dark, 0.2), review.frame_stats(busy, 0.2)
            self.assertLess(s_dark["luma"], 5)
            self.assertLess(s_dark["edges"], s_busy["edges"])
            self.assertGreater(s_busy["contrast"], 5)

    def test_grab_frame_writes_a_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = clip(Path(tmp) / "a.mp4", 1.0)
            out = review.grab_frame(p, 0.5, Path(tmp) / "f.jpg")
            self.assertTrue(out and out.is_file() and out.stat().st_size > 1000)

    def test_grab_frame_past_the_end_returns_nothing_rather_than_a_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = clip(Path(tmp) / "a.mp4", 1.0)
            out = review.grab_frame(p, 30.0, Path(tmp) / "f.jpg")
            self.assertTrue(out is None or out.stat().st_size == 0)


class ParseTest(unittest.TestCase):
    def test_plain_json(self):
        got = review.parse_json('{"a": 1}')
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["a"], 1)

    def test_fenced_json(self):
        text = 'Here you go:\n```json\n{"diagnosis": "flat", "changes": []}\n```\nHope that helps.'
        got = review.parse_json(text)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["diagnosis"], "flat")

    def test_prose_before_and_after(self):
        text = 'Sure! The issues are: {"diagnosis": "dead air", "changes": [{"priority": 1}]} — done.'
        got = review.parse_json(text)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["diagnosis"], "dead air")

    def test_nested_objects_survive(self):
        text = '{"diagnosis":"x","changes":[{"target":"a","meta":{"k":[1,2]}}]}'
        self.assertEqual(review.parse_json(text)["changes"][0]["meta"]["k"], [1, 2])

    def test_garbage_returns_none(self):
        self.assertIsNone(review.parse_json("no json at all"))


class RegistryTest(unittest.TestCase):
    def test_defaults_are_all_local_and_free(self):
        for m in review.load_models(None):
            self.assertTrue(m["base_url"].startswith("http://10.0.0."), m)

    def test_workspace_models_json_overrides_and_adds(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "models.json").write_text(json.dumps([
                {"id": "vision", "label": "Vision model", "model": "x", "base_url": "http://h/v1",
                 "vision": True}]))
            models = review.load_models(Path(tmp))
            self.assertEqual(models[0]["id"], "vision")
            self.assertTrue(models[0]["vision"])
            self.assertIn("deepseek-lane", [m["id"] for m in models])

    def test_find_model_by_id_or_model_name(self):
        self.assertEqual(review.find_model("deepseek-lane")["model"], "DeepSeek-V4.1-Flash-1017")
        with self.assertRaises(KeyError):
            review.find_model("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
