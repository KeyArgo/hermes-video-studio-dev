"""Assembly of a cut from beat clips — including beats no HTML ever produced.

What is asserted here is the contract assembly depends on:
  * a film's beats are cut in order, whoever rendered them;
  * a missing clip is reported by id, never replaced with a still or silently skipped;
  * a generative beat has no composition file, and that is not an error.

MLT is not mocked: `melt` actually runs on tiny colour clips, so the numbers are real.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from starlette.testclient import TestClient

import video_studio.films_api as films_api


def clip(path: Path, seconds: float, colour: str = "black", size: str = "1920x1080"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c={colour}:s={size}:r=30:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def probe_seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


class AssembleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "films").mkdir()
        (root / "film" / "compositions").mkdir(parents=True)
        (root / "renders" / "shots").mkdir(parents=True)
        self.root = root
        self._saved = (films_api.MOTION, films_api.FILMS, films_api.FILM_DIR,
                       films_api.RENDERS, films_api.SHOTS_OUT)
        films_api.MOTION = root
        films_api.FILMS = root / "films"
        films_api.FILM_DIR = root / "film"
        films_api.RENDERS = root / "renders"
        films_api.SHOTS_OUT = root / "renders" / "shots"

        app = FastAPI()
        app.include_router(films_api.router)
        self.client = TestClient(app)

    def tearDown(self):
        (films_api.MOTION, films_api.FILMS, films_api.FILM_DIR,
         films_api.RENDERS, films_api.SHOTS_OUT) = self._saved
        self.tmp.cleanup()

    def write_film(self, beats, total):
        (self.root / "films" / "demo.json").write_text(json.dumps(
            {"title": "Demo", "total": total, "beats": beats}))

    def test_generative_beat_needs_no_composition_file(self):
        self.write_film([{"id": "a", "duration": 2.0, "renderer": "html", "src": "compositions/a.html"},
                         {"id": "b", "duration": 2.0, "renderer": "comfy", "src": None}], 4.0)
        (self.root / "film" / "compositions" / "a.html").write_text("<template></template>")
        got = self.client.get("/films/demo").json()
        self.assertEqual([b["renderer"] for b in got["beats"]], ["html", "comfy"])
        self.assertTrue(got["beats"][0]["exists"])
        self.assertFalse(got["beats"][1]["exists"])

    def test_html_shot_render_refuses_a_generative_beat(self):
        self.write_film([{"id": "b", "duration": 2.0, "renderer": "comfy", "src": None}], 2.0)
        r = self.client.post("/films/demo/beats/b/render")
        self.assertEqual(r.status_code, 400)
        self.assertIn("comfy/beat", r.json()["detail"])

    def test_assemble_refuses_and_names_the_missing_clip(self):
        self.write_film([{"id": "a", "duration": 2.0}, {"id": "b", "duration": 2.0}], 4.0)
        clip(self.root / "renders" / "shots" / "a.mp4", 2.0)
        r = self.client.post("/films/demo/assemble", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("b", r.json()["detail"])
        self.assertNotIn("a,", r.json()["detail"])

    def test_assemble_cuts_beats_in_order_and_writes_the_film(self):
        self.write_film([{"id": "a", "duration": 2.0}, {"id": "b", "duration": 2.0}], 4.0)
        clip(self.root / "renders" / "shots" / "a.mp4", 2.0, "red")
        clip(self.root / "renders" / "shots" / "b.mp4", 2.0, "blue")
        r = self.client.post("/films/demo/assemble", json={})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["clips"], 2)
        out = self.root / "renders" / body["output"]
        self.assertTrue(out.is_file(), out)
        self.assertAlmostEqual(probe_seconds(out), 4.0, delta=0.3)

    def test_a_bed_is_attached_when_one_exists(self):
        self.write_film([{"id": "a", "duration": 2.0}], 2.0)
        clip(self.root / "renders" / "shots" / "a.mp4", 2.0)
        bed = self.root / "renders" / "bed-2.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=220:duration=2", str(bed)], check=True)
        r = self.client.post("/films/demo/assemble", json={}).json()
        self.assertEqual(r["bed"], "bed-2.wav")


if __name__ == "__main__":
    unittest.main(verbosity=2)
