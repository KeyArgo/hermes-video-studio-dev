"""Tests for the timeline model and the script validator.

The timeline is the part a person will fight with, so the checks are about behaviour, not
implementation: a split keeps both halves' source offsets, ripple closes the gap, undo is
exact, and a treatment that does not fit the target is refused instead of stretched.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_studio import ops  # noqa: E402
from video_studio.script import ScriptError, parse, to_film  # noqa: E402

CLIP_A = "/tmp/a.mp4"
CLIP_B = "/tmp/b.mp4"


def base():
    state = ops.empty("t")
    state = ops.place(state, asset=CLIP_A, track="V1", start=0, duration=4)
    return state


class TimelineTests(unittest.TestCase):
    def test_place_sets_total(self):
        state = base()
        self.assertEqual(state["total"], 4.0)
        self.assertEqual(len(state["tracks"]["V1"]), 1)

    def test_second_clip_cannot_overlap(self):
        state = ops.place(base(), asset=CLIP_B, track="V1", start=2, duration=2)
        starts = [c["start"] for c in state["tracks"]["V1"]]
        self.assertEqual(starts, [0, 4.0])          # pushed to the end of the first clip

    def test_split_keeps_source_offsets(self):
        state = ops.split(base(), state_id(), 1.5)
        left, right = state["tracks"]["V1"]
        self.assertAlmostEqual(left["duration"], 1.5, places=2)
        self.assertAlmostEqual(right["start"], 1.5, places=2)
        self.assertAlmostEqual(right["in"], 1.5, places=2)   # source moves with the split
        self.assertAlmostEqual(right["duration"], 2.5, places=2)

    def test_split_outside_clip_is_refused(self):
        with self.assertRaises(ops.OpsError):
            ops.split(base(), state_id(), 9.0)

    def test_ripple_remove_closes_the_gap(self):
        state = ops.place(base(), asset=CLIP_B, track="V1", start=4, duration=2)
        state = ops.remove(state, state_id(state), ripple=True)
        self.assertEqual(len(state["tracks"]["V1"]), 1)
        self.assertAlmostEqual(state["tracks"]["V1"][0]["start"], 0.0, places=2)

    def test_remove_without_ripple_leaves_the_hole(self):
        state = ops.place(base(), asset=CLIP_B, track="V1", start=4, duration=2)
        state = ops.remove(state, state_id(state), ripple=False)
        self.assertAlmostEqual(state["tracks"]["V1"][0]["start"], 4.0, places=2)

    def test_trim_changes_duration_not_start(self):
        state = ops.trim(base(), state_id(), duration=2.0)
        clip = state["tracks"]["V1"][0]
        self.assertEqual((clip["start"], clip["duration"]), (0.0, 2.0))
        self.assertEqual(state["total"], 2.0)

    def test_undo_restores_exact_state(self):
        workspace = Path(tempfile.mkdtemp(prefix="tl-"))
        start = base()
        ops.save(workspace, start)
        ops.push_history(workspace, "t", ops._clone(start))
        changed = ops.place(start, asset=CLIP_B, track="V2", start=0, duration=1)
        ops.save(workspace, changed)
        restored = ops.undo(workspace, "t")
        self.assertEqual(json.dumps(restored["tracks"], sort_keys=True),
                         json.dumps(start["tracks"], sort_keys=True))

    def test_unknown_track_and_op_are_refused(self):
        with self.assertRaises(ops.OpsError):
            ops.place(base(), asset=CLIP_A, track="V9", duration=1)
        with self.assertRaises(ops.OpsError):
            ops.apply_op(base(), {"op": "explode"})

    def test_to_project_maps_tracks(self):
        state = ops.place(base(), asset="/tmp/bed.wav", track="A1", start=0, duration=4)
        project = ops.to_project(state, title="t")
        kinds = [c.kind for c in project.clips]
        self.assertEqual(kinds.count("video"), 1)
        self.assertEqual(kinds.count("audio"), 1)


def state_id(state=None):
    """The id of the first clip, so the tests do not hard-code a generated name."""
    return (state or base())["tracks"]["V1"][0]["id"]


SCRIPT = {
    "title": "Aura",
    "logline": "Your desktop, alive.",
    "beats": [
        {"id": "hook", "label": "Hook", "line": "Every sound on your desktop can become light.", "seconds": 6},
        {"id": "any", "label": "Any app", "line": "It listens to anything that plays.", "seconds": 6},
        {"id": "end", "label": "End", "line": "Find it in the plugin list.", "seconds": 6},
    ],
}


class ScriptTests(unittest.TestCase):
    def test_valid_treatment_is_accepted(self):
        treatment = parse(json.dumps(SCRIPT), target=18, shots=3)
        self.assertEqual(len(treatment.beats), 3)
        self.assertEqual(treatment.duration(), 18.0)

    def test_wrong_beat_count_is_refused(self):
        with self.assertRaises(ScriptError):
            parse(json.dumps(SCRIPT), target=18, shots=5)

    def test_durations_must_match_target(self):
        with self.assertRaises(ScriptError):
            parse(json.dumps(SCRIPT), target=45)

    def test_long_line_is_refused(self):
        bad = json.loads(json.dumps(SCRIPT))
        bad["beats"][0]["line"] = "word " * 30
        with self.assertRaises(ScriptError):
            parse(json.dumps(bad), target=18, shots=3)

    def test_missing_line_is_refused(self):
        bad = json.loads(json.dumps(SCRIPT))
        bad["beats"][1]["line"] = ""
        with self.assertRaises(ScriptError):
            parse(json.dumps(bad), target=18, shots=3)

    def test_code_fence_and_prose_are_tolerated(self):
        wrapped = "Here you go:\n```json\n" + json.dumps(SCRIPT) + "\n```"
        self.assertEqual(len(parse(wrapped, target=18, shots=3).beats), 3)

    def test_to_film_matches_the_storyboard_schema(self):
        film = to_film(parse(json.dumps(SCRIPT), target=18, shots=3))
        self.assertTrue(all(b["src"].startswith("compositions/shot-") for b in film["beats"]))
        self.assertAlmostEqual(film["total"], 18.0, places=2)
        self.assertTrue(all("line" in b for b in film["beats"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
