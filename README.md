# Video Studio

Short product films, made with your agent on your own machine. A Hermes desktop plugin: seven
rooms, one film file, and a finished file that is **measured** before it is allowed to ship.

```
idea → timed beats → typed shots (HyperFrames) + real footage + generated b-roll
     → a cut that lands on the music → a measured gate → the film
```

The claim it makes is narrow and checkable: **the agent cannot say a cut is good.** Every film
carries numbers taken from the file itself — hook brightness, motion per second, loudness and true
peak, the smallest type on screen, and the share of cuts that land on a beat. Point at any moment,
type what feels wrong, and a model gets that frame's measurements plus the shot's own source, then
names the edit.

## Install

```bash
hermes plugins install KeyArgo/hermes-video-studio-dev     # add --enable on a fresh profile
```

Requires **Hermes ≥ 0.23** and **ffmpeg** on `PATH`. The studio host also needs `fastapi` and
`uvicorn` (`pip install -r requirements.txt`) — if they are missing, the plugin says so and names
that command instead of failing silently.

Then open the plugin and go to **Production → Install check**. It reports each requirement with the
command that fixes it:

| Check | Needed for | If missing |
| :-- | :-- | :-- |
| `ffmpeg` | every render and the gate | `zypper install ffmpeg` / `apt install ffmpeg` |
| `fastapi` + `uvicorn` | the studio host (the UI itself) | `pip install -r requirements.txt` |
| `melt` (MLT) | the multi-track cut | falls back to ffmpeg concat |
| `node` / `npx` | typed shots via `hyperframes` | `npx hyperframes` renders on demand |
| `wf-recorder` | recording apps that are not web pages | optional — browser capture needs nothing |
| `VIDEO_STUDIO_MOTION` | where films and renders live | defaults to `~/Development/hewn-motion` |

Nothing else is required: no account, no upload, no cloud.

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

## Desktop footage — optional, and explained in place

Some of what you want to show is not a web page: a native window, a visualiser drawn onto the
desktop, a whole session with the app you are demoing. The studio films everything with a URL using
Playwright; for the rest it needs a screen recorder on Wayland.

`wf-recorder` is that recorder. The studio never installs system packages silently and never asks
for a password itself — it offers three paths, in this order:

1. a quiet `sudo -n` attempt, when the machine allows it;
2. a normal system password dialog via `pkexec`;
3. the exact command for your distribution, with a copy button.

The Production room's **Footage sources** panel shows whether the recorder is present, what it
would be used for, and when it is the wrong tool (anything with a URL — browser capture is cleaner,
1080p, and shows no other windows).

**Record a take…** opens a sheet with the controls a real recording needs, each labelled:

| Control | Why it exists |
|---|---|
| **Aim at** | whole screen, a listed window (from `wmctrl`), or a monitor (`wf-recorder -L`) |
| **Quality** | draft / standard / high → preset + CRF; draft is for timing a take, high is for the shot that ships |
| **Frame rate** | 30 / 60 / 120. 60 matches the display; a 30 fps take of a 60 Hz screen judders in the cut |
| **Length** | 15 / 30 / 60 s or until stopped — the timer lives in the host, so a closed page cannot leave a recorder filling the disk |
| **every frame** | `--no-damage`. Without it wf-recorder only sends frames when the screen changes, which reads as stutter once it is in a 30 fps cut |
| **sound** | `--audio`, optionally a named PipeWire/Pulse source from `pactl` |
| **takes** | everything recorded here, probed (duration, size, and *broken: no video stream* when a take was cut off) |

Once it is installed the recording is a first-class source, not a file on disk:

- **aim** it at the whole screen or at a specific window (window list comes from `wmctrl`);
- **record** with an optional auto-stop (the host owns the timer, so closing the page cannot leave
  a recorder filling the disk);
- **stop** → the file is probed (a killed recorder has no moov atom and is reported as broken, not
  as done), then ingested into **Assets** like any other footage, with proxy and thumbnail;
- **use it**: *Use in beat* cuts it to the selected beat's duration, pans and dresses it, and writes
  it where assembly expects a shot — or call
  `POST /films/{id}/beats/{bid}/footage {"capture": "last"}`.

Routes: `GET /capture/record/targets`, `POST /capture/record/start`, `GET /capture/record/status`,
`POST /capture/record/stop`, `POST /capture/record/forget`.

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