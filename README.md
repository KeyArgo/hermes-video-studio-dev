# Hewn Cut

A cutting room the agent can operate. Describe the edit; the agent emits one
operation; the renderer reports measured facts. The agent never gets to say the
cut is good.

## The problem it solves

Ask an agent to "edit this video" and it writes ffmpeg it cannot verify, or
describes an edit it did not make. Both are worse than useless at 1am before a
deadline. The failure is structural: an agent is good at emitting one small
declarative operation and reading a number back. It is bad at holding a timeline
in its head.

So a project is never a pile of re-encoded files:

```json
{ "source": "takes/omniscience/hook.mp4",
  "ops": [ {"op":"tighten"}, {"op":"snip","start":4.2,"end":6.0} ] }
```

State is `replay(source, ops)`. Nothing is baked in, so undo is popping an op
(instant, no re-encode), and a re-cut is seconds. Every op carries its own data,
so replay never touches ffmpeg.

## Install

```bash
hermes plugins install KeyArgo/hermes-video-studio-dev   # private repo: run `gh auth login` first
# or locally: cp -r . ~/.hermes/plugins/hewn-video-studio
hermes plugins validate hewn-video-studio
hermes plugins enable hewn-video-studio          # tools and the tab load on the next session
```

The agent then has one tool, `video_studio`, and the dashboard gets a **Video Studio**
tab with two rooms.

Needs `ffmpeg` on PATH. The **Films** room additionally runs [HyperFrames]
(`npx hyperframes`, node >= 22, fetched on first render) against a motion workspace —
`$VIDEO_STUDIO_MOTION`, else `~/Development/hewn-motion`, else the bundled demo film, so
the room is never empty on a fresh install.

### Films room

A film is `films/<id>.json`: an ordered list of beats, each pointing at one HTML shot
composition. In the tab you get a card per shot (duration, rendered or not), **Edit**
to change that shot's HTML, **Re-render** for that shot alone (a 7 s shot takes ~31 s at
draft quality), **Render film** with live job progress, and **Gate** — the same
hook/motion/legibility checks the CLI gate runs, returned as data.

```
GET  /films                     list films + renders
GET  /films/{id}                beats, shot existence, renders
GET  /films/{id}/beats/{b}/source   PUT ... (source must keep data-composition-id)
POST /films/{id}/beats/{b}/render   one shot -> renders/shots/{b}.mp4
POST /films/{id}/render            build index + full render (job id)
GET  /jobs/{id}                 state, seconds, log tail
POST /films/{id}/gate            hook / motion / legibility on the latest render
GET  /films/{id}/video          the latest mp4
```

## The loop

```
analyze  ->  measured facts: silences, scene changes, loudness,
             suggested_tighten, hook_is_static
snip / tighten / speed / mute / caption   ->  one op each, reversible
render   ->  mp4 + the gate's checks
verify   ->  exit-1 gate on any file
```

`render` answers with `shippable` and the list of failed checks. If
`shippable` is false you fix and render again. That is the whole contract.

The gate: duration cap · 1920x1080 · 30 fps · audio present · −18…−14 LUFS ·
true peak ≤ −0.8 dB · no dead air > 1.5 s (title/end cards excluded) · no black
gap > 0.4 s · **motion in the first 5 s**.

## CLI (same operations, for testing without the agent)

```bash
python3 -m hewn_cut.cli analyze demo/before.mp4     # 3 pauses, 5.8s removable
python3 -m hewn_cut.cli new     demo/before.mp4 demo/project.json
python3 -m hewn_cut.cli tighten demo/project.json   # 20.00s -> 14.20s
python3 -m hewn_cut.cli render  demo/project.json demo/after.mp4
python3 -m hewn_cut.cli undo    demo/project.json
```

`demo/before.mp4` (20 s) and `demo/after.mp4` (14.2 s) are the proof pair.

## Files

| File | What it is |
|---|---|
| `plugin.yaml` | manifest: one tool, two config keys (`max_seconds`, `ffmpeg`) |
| `__init__.py` | `register(ctx)`, the `video_cut` schema and dispatch |
| `hewn_cut/edl.py` | project model, replay, render, verify, analyze — stdlib + ffmpeg only |
| `hewn_cut/cli.py` | the same ops from a shell |
| `tests/run_tests.py` | 9 checks, builds its own fixture, no pytest needed |
| `LIMITS.md` | what it cannot do (one track, no transitions, no ASR, …) |
| `VERIFY.md` | every claim with the command that falsifies it |

## Design notes

- **Times are on the current timeline**, not the source. A snip shifts
  everything after it; captions added before a snip do not move with it, so
  caption after the cut is locked.
- **`tighten` protects the head and tail.** Those silences are the title and end
  cards; proposing them as cuts would delete the cards.
- **`atempo` is chained** for speeds outside 0.5–2.0, so 4× works.
- **Render is a full re-encode** (~2 min for 90 s at crf 18). No proxies.


## Works with Music Studio

Video Studio does not synthesise music and does not pretend to. Beds come from
[Music Studio](https://github.com/KeyArgo/hermes-music-studio): the **Music** room reads that
plugin's take store (`~/.hermes/plugin-data/hermes-music-studio`), lists every rendered take with
its tempo and key, and attaches one to a film by pointer — track id, start offset, fades. Nothing
is copied and that store is never written to.

Music Studio links back the other way: its studio has a **Cut a video →** button that finds this
plugin's running host and opens it. Both sides discover each other's port from the other's
`studio-host.json`, so moving either one does not break the link.

## Drafts before commitment

`POST /films/{id}/drafts` renders three whole directions of a film — *quiet*, *cinematic*
(letterbox + vignette), *kinetic* (amber, harder edges) — at half frame rate and quarter size,
with the film's music already under them. A 24 s film becomes three ~1 MB files in a few minutes,
so the choice of direction is made by watching, not by imagining.


## Real footage, not impressions

`POST /capture/run` records a page being used: a list of steps (click, type, scroll, move,
evaluate), a synthetic cursor drawn and animated so the recording shows someone using the
product, then 1080p30 H.264. `POST /films/{id}/beats/{beat}/footage` turns any capture into a
beat — trimmed, slowly panned, letterboxed and graded to match the film.

A film built only from HTML a model wrote can only prove that a model can write HTML. A beat of
the product actually working is the one thing in the cut that cannot be invented.
