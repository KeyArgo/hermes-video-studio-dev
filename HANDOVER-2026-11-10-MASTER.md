# Video Studio — master handover (2026-11-10)

**Read this instead of the earlier incremental handovers.** It describes the system as it exists
right now, what is proven, what is broken, and what to do next. Supersedes
`2026-11-10-video-studio-generation-handover.md` (still useful as a detailed appendix for the
generation/comfy wiring).

Deadline context: hackathon submission **Nov 11, 15:00 MST**. Everything below runs locally, costs
nothing, and uses the user's own GPUs.

---

## 1. First five minutes of the next session

```bash
cd ~/Development/hewn-video-studio && ./scripts/deploy.sh    # copy → stamp → restart host (one command)
curl -s http://127.0.0.1:36659/api/version | python3 -m json.tool   # the build you are on
curl -s http://127.0.0.1:36659/api/films | python3 -c "import json,sys;print([f['id'] for f in json.load(sys.stdin)['films']])"
```

Then open the studio: **http://127.0.0.1:36659/** (the desktop pane frames the same URL).

The one thing blocked on the user: **`wf-recorder` is not installed**, so desktop footage (Aura,
Game Center) cannot be recorded yet. They said they will run `sudo zypper install wf-recorder`.
The studio offers this itself: Production room → *Footage sources* → **Install wf-recorder**.

---

## 2. What this is

A Hermes plugin that is a real production tool for short product films, built as a loopback
FastAPI app plus a static studio UI (no build step). Six rooms, three engines:

| Room | Does |
|---|---|
| **Films** | film list, player, per-shot re-render, gate, **review panel** ("ask a model about this moment") |
| **Assets** | intake: upload/scan/import, proxy, thumbnail, still → moving clip, trim |
| **Script** | idea → timed beats (LLM), validation, save as film, author shots |
| **Timeline** | V1/V2/A1/A2 lanes, drag/trim, exact undo, MLT render, seed from film |
| **Generate** | ComfyUI status, per-beat MiniMax H3 render, **assemble cut** |
| **Music** | tracks read from Music Studio's store, attach as bed, open Music Studio |
| **Production** | the eight ordered stages + footage sources (browser/desktop capability) |

Engines: **HyperFrames** (`hyperframes@0.9.26`) renders typed HTML/GSAP shots; **MiniMax H3 on
ComfyUI** (sparke, `10.0.0.52:8188`) renders generative beats; **MLT/`melt`** cuts beat clips +
bed into the film; **Music Studio** supplies real tracks; **Playwright** records real UI footage.

---

## 3. State right now

| Thing | Value |
|---|---|
| Build | `v1.0.0-dev.22 · 2026-11-10 · dc1d5436` (`GET /api/version`, chip in the header) |
| Repo | `KeyArgo/hermes-video-studio-dev`, private, master `dc1d543`, 22 commits |
| Tests | **78 green** — timeline/script 17, assets/comfy 10, gen_beat 9, assemble 5, music/drafts 9, review 13, legacy 15 |
| Studio host | `127.0.0.1:36659`, state in `~/.hermes/plugin-data/hewn-video-studio/studio-host.json` |
| Routes | 55 (`GET /openapi.json`) |
| Music Studio | `127.0.0.1:8775`, linked both ways, 4 takes available |
| ComfyUI | sparke `10.0.0.52:8188`, H3 fp8 + Wan I2V + Music3, no image checkpoint |
| Local model lanes | DeepSeek V4.1 Flash `10.0.0.50:8890`, Ollama `10.0.0.52:11434` (43B q6, dsv4 q2k) |

Films in `~/Development/hewn-motion/films/`:

| Film | Total | Beats | State |
|---|---|---|---|
| `aura-sound-made-visible` | 24 s | 4 | **shippable** — cinematic treatment, music *Cursor Blink*, gate green (hook 14.9 / motion 4.26 % / −14.5 LUFS / 34 px) |
| `aura-mixed` | 24 s | 4 | 3 typed + 1 H3-generated beat, gated green (proves mixed engines) |
| `aura` | 45 s | 5 | older 45 s cut, not re-gated since the gate gained audio |
| `gameshelf` | 5 beats | — | Game Center film, no footage, weakest of the set |

Shipped to `/mnt/AllShare/dev/videos/hackathon-2026-11-11/`: Aura 24 s, Aura mixed 24 s, Game
Center 45 s, Aura 45 s, `studio-walkthrough-1080p.mp4`, `drafts/aura-draft-{quiet,cinematic,kinetic}.mp4`,
`stills/hero-desk-glow.png`.

---

## 4. Subsystems, with the proof each one works

| Subsystem | Files | Endpoint(s) | Evidence it works |
|---|---|---|---|
| Films + gate | `video_studio/films_api.py` | `/films`, `/films/{id}`, `/gate`, `/render`, `/video` | 24 s cut gated green after 6 render cycles |
| Shot generation | `shotgen.py` | `/script/{id}/shots` | 4 shots, 0 refused, 125 s; contract refuses bad HTML |
| Script | `script.py`, `script_api.py` | `/script/draft`, `/save`, `/check` | 24 s / 4 beats drafted by the lane in ~16 s |
| Timeline | `ops.py`, `timeline_api.py`, `mlt_project.py` | `/timeline/{id}/*` | 2-clip 5 s cut rendered in 13.6 s; undo byte-identical |
| Assets | `assets_api.py` | `/assets/*`, `/stills/scan`, `/assets/import` | upload 2→3; still 1245×952 → 4 s 1080p30 clip; trim keeps audio |
| H3 generation | `comfy.py`, `comfy_api.py`, `gen_beat.py` | `/comfy/status`, `/comfy/beat` | 169 frames → 7 s clip, **263 s**, brand-graded |
| Music bridge | `music.py` | `/music/library`, `/films/{id}/music` | reads 4 takes read-only; bed −36.6 → −14.5 LUFS |
| Drafts | `drafts.py` | `/films/{id}/drafts` | 3 directions of a 24 s film in ~4 min, ~1 MB each |
| Capture (web) | `capture.py`, `capture.js` | `/capture/run`, `/capture/windows` | 11-step walkthrough, 18.6 s 1080p30, 0 console errors |
| Capture (desktop) | `capture.desktop()` | `/capture/desktop`, `/capture/deps`, `/capture/install` | **blocked**: wf-recorder missing; deps panel + install offer work |
| Footage beat | `films_api.beat_from_footage` | `/films/{id}/beats/{bid}/footage` | turned the studio capture into beat 2 (then reverted — wrong product) |
| Model review | `review.py` | `/models`, `/review` | DeepSeek at 12.5 s: motion 5.99 %/s vs 16.2 %, named the ring tweens |
| Versioning | `build_info.py` | `/version` | chip `v1.0.0-dev.22 · 2026-11-10 · dc1d5436` |

---

## 5. The pipeline, and why it exists

The failure mode of "AI video" is that a language model can only produce text-shaped pixels: words
typeset nicely, nothing on screen proving anything. The pipeline deliberately surrounds the words
with things a model cannot invent:

1. **Beats** (LLM) — structure and wording, where models are strong.
2. **Typed shots** (HyperFrames + one brand system) — constrained by a written contract: first
   frame shows the headline, every tween has an explicit time position, motion every second.
3. **Real footage** (Playwright or desktop recorder) — the product actually working.
4. **Generated b-roll** (H3) — atmosphere no camera could get, graded to the brand.
5. **Stills** (image model → asset) — hero frames with negative space.
6. **Music** (Music Studio) — a real track, not a drone.
7. **Drafts** — three cheap directions, watched, then one promoted by `POST /films/{id}/treatment`.
8. **Gate** — hook / motion / audio / legibility, measured on the finished file.

Rule of thumb that has held all session: **every "cinematic" step costs brightness** (letterbox,
vignette, dark palette). The gate is what catches it.

---

## 6. Operating ritual

```bash
# deploy (copy + stamp + restart) — the installed tree does NOT follow the source
cd ~/Development/hewn-video-studio && ./scripts/deploy.sh

# tests
python3 tests/run_tests.py            # legacy 15
python3 tests/test_review.py          # 13  (also: test_timeline_script, test_assets_comfy,
                                     #       test_gen_beat, test_assemble, test_music_drafts)

# a film, end to end
curl -X POST localhost:36659/api/script/$FILM/shots -d '{"only_missing":true}' -H 'Content-Type: application/json'
curl -X POST localhost:36659/api/films/$FILM/assemble   -H 'Content-Type: application/json' -d '{}'
curl -X POST localhost:36659/api/films/$FILM/gate
```

Versioning: `plugin.yaml` holds the **release target** (`1.0.0`); `BUILD_INFO.json` is generated,
gitignored, and holds `1.0.0-dev.<commit count>`. Installed copies with no git history keep the
source stamp (never `-dev.0`).

---

## 7. Known limits and defects — read before promising anything

1. **Desktop capture is blocked.** No `wf-recorder`/`grim`/`xdg-desktop-portal`; X11 grab of the
   rootless Xwayland returns ~2/255 luma. Aura and Game Center render *inside the Hermes desktop*,
   so they cannot be filmed until the recorder exists. `/capture/deps` states this; the UI offers
   the install (quiet sudo → pkexec prompt → command).
2. **Aura's page is host-driven.** `web/index.html` connects to `/api/stream` and reads
   `--aura-level`; live in the shell it animates, opened in a browser it draws a still frame
   (0.00 % motion over 8 s; 0.03–0.12 % even fed a real RMS envelope). Do not "capture" it in the
   browser and call it footage.
3. **Game Center has no standalone URL.** Its games are Python-rendered panels; `/view` on port
   5555 is sparkDash, not gameshelf.
4. **Drafts are treatments, not new ideas.** They change palette, letterbox/vignette, and accent
   behaviour. If a draft is rejected, the fix is rewriting beats, not re-tinting.
5. **H3 output is 768×448 upscaled to 1080p** — soft at full size. Render 1344×768 for hero frames
   (2–3× the time). 169 frames took 263 s.
6. **Audio is normalised at assembly, not at source** — the loose `bed-*.wav` files are quiet by
   design (−36 LUFS); `mlt_project.normalise_audio` fixes the cut to −18…−14.
7. **Timeline vs film is unresolved.** `from-film` seeds V1; editing the timeline does not write
   back to `films/<id>.json`. Decide which is authoritative before wiring both editable.
8. **Plugin id mismatch** — repo `hermes-video-studio-dev`, internal id `hewn-video-studio`. Harmless;
   leave until after the deadline.
9. **`~/.hermes/config.yaml` is agent-immutable** — the user must run `hermes config set …`.
10. **MoA stays off** (its aggregator bills Claude Opus).
11. **Local lanes are text-only**, so `/review` does not send a frame there; the panel says so. A
    vision model can be added through `models.json` in the film workspace.

---

## 8. Next steps, in order

1. **Install `wf-recorder`** (user, one command or the button in Production). Then:
   `curl -X POST localhost:36659/api/capture/desktop -d '{"name":"aura-live","seconds":12,"window":"Hermes"}'`
   → `POST /films/aura-sound-made-visible/beats/shot-listens-to-anything/footage` with that capture.
   That is the shot the user asked for and did not get (beat 2 currently typed).
2. **Game Center film**: capture the jackpot/dashboard pane the same way, rebuild the film so it
   *opens* with real footage (the user's "yes"), then gate.
3. **Promote review into action**: `/review` advises; an obvious next step is proposed patches
   (a diff on the shot's HTML) applied only on approval.
4. **Make the bed automatic per film length** (`tools/audio.py` still manual for ad-hoc beds;
   the Music Studio path is the supported one).
5. **Decide film-vs-timeline authority** and remove the other editing surface.
6. Then the quality pass: the typed shots are still "slides with copy" — replace 1–2 per film with
   footage or b-roll before adding more polish to type.

---

## 9. Paths, ports, environment

```
plugin source      ~/Development/hewn-video-studio            (git → KeyArgo/hermes-video-studio-dev)
installed copy     ~/.hermes/plugins/hewn-video-studio        (copied; deploy.sh keeps it in sync)
desktop pane       ~/.hermes/desktop-plugins/hewn-video-studio/plugin.js
motion workspace   ~/Development/hewn-motion                  (VIDEO_STUDIO_MOTION overrides)
  films/           film JSON: beats, renderer per beat, treatment, music pointer
  film/            HyperFrames project (index.html + compositions/)
  renders/         <film>-final.mp4, shots/<beat>.mp4, drafts/, captures/, reviews/
  assets/stills/   drop generated stills here → /stills/scan
music store        ~/.hermes/plugin-data/hermes-music-studio  (read-only; MUSIC_STORE overrides)
studio host state  ~/.hermes/plugin-data/hewn-video-studio/studio-host.json
models overlay     <motion workspace>/models.json             (adds/overrides /models entries)
```

Gate implementation: `~/Development/hewn-motion/tools/gate.py` (hook with centre-crop + dark-but-lit
rule, motion per second at 320×180, audio −18…−14 LUFS & TP ≤ −1, legibility from composition CSS,
in-progress guard).

---

## 10. Traps that cost time this session

- **The host caches Python**: restart after any backend change or you debug the previous version.
- **HyperFrames refuses a composition that is not mounted from `index.html`** and will not render
  one outside the project directory — a draft must be its own mini-project.
- **A tween without a position argument is appended after the previous one** — motion slides past
  the end of the shot and the frame goes still.
- **A fade-in on the first headline** puts an empty frame at the top of the film; the hook check
  fails and it is right to.
- **A render that is still being written has no moov atom** — the gate now says so instead of
  reporting "no audio".
- **`melt` mixes a bed at file level** — quiet beds arrive 20 dB down and play as silence.
- **Letterbox/vignette on a dark UI measures 3/255** — dress the frame, do not bury it.
- **Two assembles can race on the same output** — check the file has stopped growing before gating.

---

## 11. This session's commits (`hewn-video-studio`, newest first)

`dc1d543` review + capture deps/install · `55ab205` desktop capture route + window discovery ·
`852d1d9` capture.desktop with honest failure · `ddcf4f5` versioning + deploy script ·
`ad48a0d` footage beats (pan, letterbox, lift) · `14c2d66` capture + stills + Production room +
cinematic treatment + gate audio/letterbox/in-progress · `870487e` music bridge + drafts + mutual
cross-links · `c1bd809` gen_beat timing · `54dc296` generated beats + assembly + audio normalise ·
`08314cf` docs correction.

`hewn-motion`: `c7d79b4` footage beat · `7a11a50` gate (audio, centre-crop, dark-but-lit,
in-progress) + hook shot visible at frame 0 · `d0c17c1` music bed + drafts · `967a196` audio gate +
mixed-engine film · `65a380f` film passes gate.
