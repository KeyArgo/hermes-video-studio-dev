"""Tests for the generative-beat path: prompt shape, frame maths, and normalisation.

The ComfyUI call itself is not mocked into passing — a fake that "succeeds" proves nothing about
the GPU. What is tested here is everything that can be checked without one: the beat becomes the
right prompt, the frame count is legal, and any clip of any size becomes a 1080p30 clip of the
exact beat duration.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_studio.gen_beat import (OUT_FPS, OUT_H, OUT_W, beat_prompt, frames_for,
                                  normalise)


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=width,height,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    import json
    data = json.loads(out)
    s = data["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"w": s["width"], "h": s["height"], "fps": round(int(num) / int(den), 3),
            "duration": round(float(data["format"]["duration"]), 2)}


class PromptTest(unittest.TestCase):
    def test_director_note_and_text_both_used(self):
        p = beat_prompt({"shot": "a wall of light", "line": "Sound becomes colour"})
        self.assertIn("a wall of light", p)
        self.assertIn("Sound becomes colour", p)
        self.assertIn("no text", p.lower())

    def test_note_only_still_carries_no_text_rule(self):
        p = beat_prompt({"shot": "slow dolly over a desk"})
        self.assertIn("slow dolly", p)
        self.assertTrue(p)

    def test_empty_beat_does_not_produce_an_empty_prompt(self):
        self.assertTrue(beat_prompt({}).strip())

    def test_line_is_never_sent_as_lettering(self):
        p = beat_prompt({"shot": "x", "line": "BIG WORDS"})
        self.assertNotIn("BIG WORDS to camera", p)


class FrameMathTest(unittest.TestCase):
    def test_five_seconds_is_about_120_frames(self):
        self.assertAlmostEqual(frames_for(5.0) / 24, 5.0, delta=0.15)

    def test_never_below_one_second(self):
        self.assertGreaterEqual(frames_for(0.2), 17)

    def test_counts_stay_odd(self):
        for secs in (2, 3.5, 5, 6, 7, 8.25):
            self.assertEqual(frames_for(secs) % 2, 1, secs)


class NormaliseTest(unittest.TestCase):
    def test_any_source_clip_becomes_1080p30_of_the_beat_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "testsrc=size=768x448:rate=24:duration=8",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
            dest = Path(tmp) / "out.mp4"
            res = normalise(src, dest, 3.0)
            got = probe(dest)
            self.assertEqual(res["width"], OUT_W)
            self.assertEqual(got["w"], OUT_W)
            self.assertEqual(got["h"], OUT_H)
            self.assertEqual(got["fps"], float(OUT_FPS))
            self.assertAlmostEqual(got["duration"], 3.0, delta=0.15)

    def test_a_short_source_is_not_stretched_silently(self):
        """A 1 s clip asked to fill 4 s must fail loudly or fill, never pretend."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "testsrc=size=320x240:rate=24:duration=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
            dest = Path(tmp) / "out.mp4"
            normalise(src, dest, 4.0)
            self.assertLess(probe(dest)["duration"], 4.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
