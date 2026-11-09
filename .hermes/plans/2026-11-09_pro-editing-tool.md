# Plan — Video Studio as a real editing tool, with open source doing the hard parts

2026-11-09 · reads with `studio/`, `video_studio/mlt_project.py`, `studio_host.py`
Deadline context: films still due for Nov 11; the studio must be usable *while* it grows.

## Goal

A studio you can cut in: multiple tracks, clips you can trim and move, transitions, an audio
mixer, a scrubber that plays, undo, and a project file that outlives the tool — built on
engines that already exist rather than re-implemented in JavaScript.

## What "professional" means here (checkable, not adjectives)

| Capability | Today | Target | Judged by |
|---|---|---|---|
| Timeline with tracks | shots in a row, fixed order | V1/V2 + A1/A2, clips at free positions | a clip can start at 3.4 s on V2 over V1 |
| Trim / slip / ripple | none | drag a clip's edge; minute-long source trims | source in/out reflected in the project file |
| Transitions | none | dissolve, wipe, dip-to-black | rendered output shows the overlap |
| Audio | one bed | per-clip gain, ducking, fades, LUFS | −16 LUFS measured on output |
| Keyframes | none | opacity/scale/gain envelopes | frame at 50 % of envelope is 50 % |
| Undo | none | every operation reversible | N ops then N undos returns the same XML |
| Project format | `films/*.json` | OTIO round-trip | `otio` file reopens with identical timing |
| Preview | full res only | proxy (540p) playback | scrub without waiting on 1080p |
| Render | HyperFrames or ffmpeg | melt, deterministic | byte-stable re-render |

## The open-source stack, and the gap each one closes

| Piece | Licence | Installed here | What it gives us | What we still write |
|---|---|---|---|---|
| **MLT / melt 7.38** | LGPL-2.1 | ✅ | the engine behind Shotcut & Kdenlive: multitrack XML, dissolves, keyframes, audio mixing, x264 output | the XML writer + a project model |
| **OpenTimelineIO 0.18** | Apache-2.0 | ✅ (`.venv`) | project interchange: OTIO ⇄ EDL/AAF/FCP XML, so an edit survives leaving the tool | mapping OTIO ⇄ our model |
| **GStreamer 1.28** | LGPL | ✅ | real-time preview path; later, a proper scrubbable player | a preview pipeline |
| **ffmpeg 6.1.3** | LGPL/GPL build | ✅ | proxies, thumbnails, audio analysis, loudnorm | the job plumbing |
| **auto-editor** | Unlicense | ✗ (pip) | silence-based cuts, used as a *test oracle* for our tighten op | — |
| **Whisper / faster-whisper** | MIT | ✗ | captions from audio instead of hand-typed | optional phase |
| **libebur128 (via ffmpeg)** | MIT | ✅ | −16 LUFS gate that already exists | — |

Explicitly **not** adopted: Remotion (licence), MoviePy (no timeline model), libopenshot
(duplicates MLT with a heavier binding), OpenShot/Kdenlive as shells (we keep their engine,
not their UI).

## Proven today (not planned — measured)

- `melt` rendered a 45.0 s, 1920×1080, 30 fps film from an MLT XML built by
  `video_studio/mlt_project.py`: **4.4 MB, 32 s wall**.
- Two HyperFrames shots (7 s + 10 s) placed at 0 s and 7 s with a **0.6 s dissolve** between
  them; frame luma proves the picture is there (15.3 → 10.7 at the cut → 17.2).
- A missing beat leaves **black** (luma 0.0), not a white flash — found by measuring, fixed
  with an explicit `colour:black` base track.
- The same project written as **OTIO** and read back: video clips at 0.0 s / 7.0 s with
  7.0 s / 10.0 s durations, audio 45.0 s, name preserved.

## Architecture

```
studio/            the app (vanilla HTML/CSS/JS served by the loopback host)
video_studio/
  films_api.py     films: list, beats, shot source, render job, gate      (exists)
  studio_host.py   uvicorn app: /api + the studio app                     (exists)
  host_launcher.py starts the host, writes studio-host.json {port,pid}    (exists)
  mlt_project.py   Project model -> MLT XML -> melt; OTIO in/out          (exists, minimal)
  timeline_api.py  NEW: /timeline routes — tracks, clips, ops, undo, render
  ops.py           NEW: the operation list (place, trim, move, ripple, gain, fade)
```

The studio keeps one rule from the rest of the project: **the UI never computes timing**.
It sends operations; the backend owns the model and returns the resulting timeline.

## Phases

### Phase A — the timeline model (2–3 h)

1. `ops.py`: operations as data — `place(clip, track, at)`, `trim(clip, in, out)`,
   `move(clip, at)`, `ripple(gap)`, `gain(clip, db)`, `fade(clip, in, out)` — each returning
   a new project dict; no mutation.
2. `timeline_api.py`: `GET /timeline/{film}`, `POST /timeline/{film}/ops` (list), `POST
   /timeline/{film}/undo`, `POST /timeline/{film}/render`.
3. `Project` gains `tracks: {"V1": [...], "V2": [...], "A1": [...]}`; `from_beats` becomes
   one importer among several.
4. **Tests** (`tests/test_mlt_project.py`): dissolve maths; black base track; trim reflected
   in frames; 5 ops + 5 undos == original XML; OTIO round-trip equality on duration.
5. **Accept:** `pytest tests/test_mlt_project.py` green; a 12-clip project renders and the
   probe shows the expected duration ±1 frame.

### Phase B — the timeline UI (3–4 h)

1. Canvas timeline: ruler in seconds, tracks V1/V2/A1/A2, clips as rectangles with a
   thumbnail strip (ffmpeg, 8 thumbs per clip).
2. Drag to move, drag edges to trim, drag on the ruler to scrub; a playhead that scrubs a
   **proxy** (540p, generated once per clip) so scrubbing never waits on 1080p.
3. Keyboard: space, J/K/L, arrow nudge, `S` split at playhead, `Cmd+Z` undo.
4. **Accept:** move a clip, split it, undo twice — the JSON returns to the start; the
   playhead scrub plays video within 150 ms of the drag.

### Phase C — transitions and audio (2–3 h)

1. Dissolve, dip-to-black, wipe — as MLT transitions with a duration parameter.
2. Per-clip gain, fades, and a **ducking** op on the bed keyed to voice spans; loudness
   normalisation at render.
3. **Accept:** render with two dissolves + a ducked bed; loudness −16 ±0.5 LUFS; audible
   dip at each cut (measured as ≥3 dB on the bed band).

### Phase D — the film generator plugs in (2 h)

1. `films/<id>.json` imports into the timeline as V1 clips at the beat positions.
2. Beats can be re-rendered from the timeline (existing per-shot render), so "regenerate
   this shot" and "trim this shot" live in the same place.
3. **Accept:** import Aura, move one shot, render — the gate still passes.

### Phase E — respect in the interface

1. Every panel shows measured values (durations, LUFS, frame counts), never estimates.
2. Empty states name the next action; no invented progress.
3. **Accept:** a stranger can go from "open studio" to "rendered film" without a doc.

## Risks

1. **MLT XML is undocumented at the edges** — dissolves and black gaps were both wrong on
   the first attempt and only measurement caught them. Every new effect gets a frame-level
   test, not a "looks right".
2. **OTIO's EDL/AAF round-trip loses effects** — treat OTIO as the *timing* interchange, and
   document that effects flatten on export.
3. **Two renderers** (HyperFrames for shots, MLT for the cut) means a shot's look is decided
   before the timeline sees it; do not try to render HTML inside MLT.
4. **Proxy drift** — proxies must be generated from the same source frames, never re-timed.
5. **Scope creep** — the deadline is Nov 11; Phase A+B unblock the studio, C–E can follow.
