"""Cutting to the beat: the grid, the phase, and shot lengths that land on it.

The arithmetic is the product here — a cut 40 ms off a beat reads as sloppy, and a "fit" that
changed the film's length would slide the whole bed. Both are cheap to test.
"""
from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_studio import beatfit


def click_track(path: Path, bpm: float, seconds: float, phase: float, sr: int = 22050) -> Path:
    """A metronome: 12 ms bursts exactly on the beat, shifted by `phase`."""
    step = 60.0 / bpm
    n = int(seconds * sr)
    samples = [0] * n
    t = phase
    while t < seconds:
        i = int(t * sr)
        for k in range(int(0.012 * sr)):
            if i + k < n:
                samples[i + k] = int(12000 * math.exp(-k / (0.004 * sr)))
        t += step
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack("<%dh" % n, *samples))
    return path


class TempoTest(unittest.TestCase):
    def test_beat_length_follows_bpm(self):
        self.assertAlmostEqual(beatfit.beat_length(90), 0.6667, places=3)
        self.assertAlmostEqual(beatfit.beat_length(120), 0.5, places=3)

    def test_no_tempo_is_a_refusal_not_a_default(self):
        for bad in (None, 0, -5):
            with self.assertRaises(ValueError):
                beatfit.beat_length(bad)

    def test_grid_covers_the_film(self):
        g = beatfit.grid(4.0, 120)                       # 0.5 s beats
        self.assertEqual(len(g), 8)
        self.assertAlmostEqual(g[0], 0.5)
        self.assertAlmostEqual(g[-1], 4.0)


class PhaseTest(unittest.TestCase):
    def test_offset_is_measured_from_the_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = click_track(Path(tmp) / "click.wav", 90, 8.0, phase=0.25)
            got = beatfit.offset(p, 0.0, 90)
            self.assertEqual(got["source"], "audio")
            # 0.25 s phase at a 0.6667 s beat: the estimator may fold it, so compare on the grid
            step = 60.0 / 90
            folded = min(abs(got["offset"] - 0.25), step - abs(got["offset"] - 0.25))
            self.assertLess(folded, 0.06, got)

    def test_missing_file_says_so_instead_of_inventing_a_phase(self):
        got = beatfit.offset(None, 0.0, 90)
        self.assertEqual(got["source"], "none")
        self.assertEqual(got["offset"], 0.0)


class FitTest(unittest.TestCase):
    def test_a_cut_off_the_beat_is_reported_off(self):
        marks = beatfit.on_beat([7.5 * 0.6667, 16.5 * 0.6667, 24.0], 90, 0.0)
        self.assertEqual(marks["of"], 2)
        self.assertEqual(marks["on"], 0)

    def test_fit_puts_every_cut_on_a_beat_and_keeps_the_length(self):
        fitted = beatfit.fit([5.0, 6.0, 7.0, 6.0], 90.0, offset_s=0.0)
        self.assertTrue(fitted["ok"], fitted)
        self.assertAlmostEqual(sum(fitted["durations"]), 24.0, places=2)
        bounds, acc = [], 0.0
        for d in fitted["durations"]:
            acc += d
            bounds.append(acc)
        marks = beatfit.on_beat(bounds, 90.0, 0.0)
        self.assertEqual(marks["on"], marks["of"], marks)

    def test_fit_never_makes_a_flash(self):
        fitted = beatfit.fit([2.0, 2.0, 20.0], 200.0, offset_s=0.3)   # 0.3 s beats
        self.assertTrue(fitted["ok"], fitted)
        for d in fitted["durations"]:
            self.assertGreaterEqual(d, beatfit.MIN_BEAT - 1e-6, fitted)

    def test_a_single_shot_film_has_nothing_to_fit(self):
        out = beatfit.fit([12.0], 90.0)
        self.assertFalse(out["ok"])
        self.assertIn("one shot", out["reason"])

    def test_already_fitted_film_moves_nothing(self):
        first = beatfit.fit([5.0, 6.0, 7.0, 6.0], 90.0, 0.0)
        second = beatfit.fit(first["durations"], 90.0, 0.0)
        self.assertEqual(second["moved"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
