"""Tests for the intake room and the ComfyUI bridge.

What is worth testing here: that a screenshot becomes a real 1080p moving clip, that a
recording can be trimmed without losing audio, that a bad graph fails loudly, and that
parameter substitution touches the nodes it names and nothing else.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKSPACE = Path(tempfile.mkdtemp(prefix="vs-assets-"))
os.environ["VIDEO_STUDIO_MOTION"] = str(WORKSPACE)
(WORKSPACE / "assets" / "intake").mkdir(parents=True, exist_ok=True)
(WORKSPACE / "films").mkdir(parents=True, exist_ok=True)

from video_studio import assets_api  # noqa: E402
from video_studio.comfy import ComfyError, load_graph, substitute  # noqa: E402


def make_png(path: Path, size=(800, 600)):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=0x1b6ef3:s={size[0]}x{size[1]}:d=1", "-frames:v", "1",
                    str(path)], check=True)
    return path


def make_mp4(path: Path, seconds=2):
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return path


class IntakeTests(unittest.TestCase):
    def test_image_ingested_with_thumb_and_no_proxy(self):
        record = assets_api.ingest(make_png(WORKSPACE / "shot.png"))
        self.assertEqual(record["kind"], "image")
        self.assertEqual(record["probe"]["width"], 800)
        self.assertTrue(Path(record["thumb"]).is_file())
        self.assertIsNone(record["proxy"])

    def test_video_ingested_with_proxy_and_audio_flag(self):
        record = assets_api.ingest(make_mp4(WORKSPACE / "rec.mp4", 2))
        self.assertEqual(record["kind"], "video")
        self.assertTrue(record["probe"]["has_audio"])
        self.assertTrue(Path(record["proxy"]).is_file())
        # proxy is 540p, as promised to the scrubber
        probe = assets_api.probe(Path(record["proxy"]))
        self.assertEqual(probe["height"], 540)

    def test_still_becomes_moving_1080p_clip(self):
        record = assets_api.ingest(make_png(WORKSPACE / "still.png", (1245, 952)))
        result = assets_api.still_clip(record["id"], seconds=2.0, direction="in")
        probe = result["probe"]
        self.assertAlmostEqual(probe["duration"], 2.0, places=1)
        self.assertEqual((probe["width"], probe["height"]), (1920, 1080))
        self.assertEqual(probe["fps"], 30.0)

    def test_still_refuses_a_video(self):
        record = assets_api.ingest(make_mp4(WORKSPACE / "notastill.mp4", 1))
        with self.assertRaises(Exception):
            assets_api.still_clip(record["id"], seconds=1.0)

    def test_trim_keeps_audio_and_length(self):
        record = assets_api.ingest(make_mp4(WORKSPACE / "trimme.mp4", 3))
        result = assets_api.trim(record["id"], 0.5, 2.0)
        self.assertAlmostEqual(result["probe"]["duration"], 1.5, places=1)
        self.assertTrue(result["probe"]["has_audio"])

    def test_trim_rejects_backwards_range(self):
        record = assets_api.ingest(make_mp4(WORKSPACE / "bad.mp4", 1))
        with self.assertRaises(Exception):
            assets_api.trim(record["id"], 1.0, 0.5)

    def test_scan_is_idempotent(self):
        make_png(WORKSPACE / "assets" / "intake" / "dropped.png")
        first = assets_api.scan()
        self.assertIn("dropped", " ".join(first["added"]))
        second = assets_api.scan()
        self.assertEqual(second["added"], [])
        self.assertTrue(second["skipped"])


class GraphTests(unittest.TestCase):
    GRAPH = {
        "3": {"class_type": "UNETLoader", "_meta": {"title": "Load H3"},
              "inputs": {"unet_name": "minimax_h3.safetensors", "weight_dtype": "default"}},
        "5": {"class_type": "CLIPTextEncode", "_meta": {"title": "Prompt"},
              "inputs": {"text": "plain", "clip": ["3", 0]}},
    }

    def test_substitute_by_title(self):
        out = substitute(self.GRAPH, {"Prompt": {"text": "a blue orb"}, "Load H3": {"weight_dtype": "fp8"}})
        self.assertEqual(out["5"]["inputs"]["text"], "a blue orb")
        self.assertEqual(out["3"]["inputs"]["weight_dtype"], "fp8")
        self.assertEqual(self.GRAPH["5"]["inputs"]["text"], "plain")   # input not mutated

    def test_substitute_by_node_id(self):
        out = substitute(self.GRAPH, {"5": {"text": "by id"}})
        self.assertEqual(out["5"]["inputs"]["text"], "by id")

    def test_load_graph_rejects_junk(self):
        path = WORKSPACE / "junk.json"
        path.write_text("[]")
        with self.assertRaises(ComfyError):
            load_graph(path)


def tearDownModule():
    shutil.rmtree(WORKSPACE, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
