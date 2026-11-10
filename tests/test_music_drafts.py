"""Music bridge and treatment drafts: the two things a film does around its shots.

Tested without the GPU: the music library is a directory of JSON + WAV, and a draft is a text
transformation plus a cheap render. What matters is that a track is found only when its audio
really exists, that a short track is looped rather than left silent, and that re-paletting
touches brand colours without repainting someone's product art.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import video_studio.drafts as drafts
import video_studio.music as music


def wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=330:duration={seconds}", str(path)], check=True)
    return path


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


class MusicLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._env = os.environ.get("MUSIC_STORE")
        os.environ["MUSIC_STORE"] = str(self.root)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("MUSIC_STORE", None)
        else:
            os.environ["MUSIC_STORE"] = self._env
        self.tmp.cleanup()

    def project(self, pid: str, takes: list[str], title: str = "Cursor Blink"):
        (self.root / f"{pid}.json").write_text(json.dumps({
            "project_id": pid, "title": title, "bpm": 90, "keyscale": "A minor",
            "takes": [{"take_id": t} for t in takes]}))

    def test_a_take_without_audio_is_not_listed(self):
        self.project("p1", ["t1", "t2"])
        wav(self.root / "t1.wav", 2)
        tracks = music.library()
        self.assertEqual([t["id"] for t in tracks], ["t1"])

    def test_track_carries_the_metadata_a_cut_needs(self):
        self.project("p1", ["t1"])
        wav(self.root / "t1.wav", 1.5)
        t = music.library()[0]
        self.assertEqual(t["title"], "Cursor Blink")
        self.assertEqual(t["bpm"], 90)
        self.assertAlmostEqual(t["seconds"], 1.5, delta=0.2)

    def test_a_missing_store_is_empty_not_an_error(self):
        os.environ["MUSIC_STORE"] = str(self.root / "nope")
        self.assertEqual(music.library(), [])

    def test_bed_is_cut_to_length_with_fades(self):
        self.project("p1", ["t1"])
        wav(self.root / "t1.wav", 10)
        track = music.library()[0]
        out = self.root / "bed.wav"
        res = music.bed_from_track(track, out, 4.0, start=2.0, fade_in=0.5, fade_out=0.5)
        self.assertAlmostEqual(duration(out), 4.0, delta=0.2)
        self.assertFalse(res["looped"])

    def test_a_short_track_is_looped_rather_than_silent(self):
        self.project("p1", ["t1"])
        wav(self.root / "t1.wav", 1.0)
        track = music.library()[0]
        out = self.root / "bed.wav"
        res = music.bed_from_track(track, out, 3.0)
        self.assertTrue(res["looped"])
        self.assertAlmostEqual(duration(out), 3.0, delta=0.25)


class DraftTest(unittest.TestCase):
    def test_brand_colours_swap_but_content_colours_stay(self):
        html = ('<style>#root{background:#0b0d10;color:#e8eaed}'
                '.badge{background:#7c3aed;color:#f0abfc}</style>')
        out = drafts._repalette(html, drafts.VARIANTS["cinematic"])
        self.assertIn(drafts.VARIANTS["cinematic"]["bg"], out)
        self.assertIn(drafts.VARIANTS["cinematic"]["ink"], out)
        self.assertIn("#7c3aed", out, "a product's own colour must survive")
        self.assertIn("#f0abfc", out)

    def test_variants_are_actually_different(self):
        accents = {v["accent"] for v in drafts.VARIANTS.values()}
        self.assertEqual(len(accents), len(drafts.VARIANTS),
                         "two variants with the same accent are one variant")

    def test_treatment_css_is_injected_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            film_dir = root / "film"
            (film_dir / "compositions").mkdir(parents=True)
            (film_dir / "compositions").mkdir(parents=True, exist_ok=True)
            (film_dir / "compositions" / "a.html").write_text(
                "<template><style>#root{background:#0b0d10}</style><div id='root'></div></template>")
            (film_dir / "index.html").write_text(
                '<div id="root"><div data-composition-src="compositions/a.html"></div></div>')
            film = {"beats": [{"id": "a", "src": "compositions/a.html"}]}
            project = drafts.build("demo", "cinematic", film, film_dir, root / "out")
            html = (project / "compositions" / "a.html").read_text()
            self.assertTrue((project / "index.html").is_file())
            self.assertEqual(html.count("letterbox"), 1)
            self.assertIn("cinematic", drafts.VARIANTS)

    def test_unknown_variant_is_refused_by_the_builders_contract(self):
        with self.assertRaises(KeyError):
            drafts.VARIANTS["nope"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
