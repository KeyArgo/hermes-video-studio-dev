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
cp -r ~/Development/hewn-cut ~/.hermes/plugins/hewn-cut
hermes plugins validate ~/.hermes/plugins/hewn-cut
hermes plugins enable hewn-cut          # tools load on the next session
```

The agent then has one tool, `video_cut`.

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
