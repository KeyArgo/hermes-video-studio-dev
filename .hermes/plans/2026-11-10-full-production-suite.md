# The full production suite — what actually makes AI video, and what this plugin has

2026-11-10 · companion to `2026-11-09_pro-editing-tool.md`
Question this answers: what do the people who make good AI promos, AI-influencer content,
AI music videos and short films actually do, and which of those steps exist here?

Status words are strict: **built** = in this repo, exercised; **partial** = some of it works;
**planned** = designed, not written.

---

## 1. The chain, top to bottom

| # | Stage | What the good ones do | Here today | Filled by |
|---|---|---|---|---|
| 1 | **Concept / script** | one idea, 8–14 beats, a spoken line per beat, written for the ear | partial — scripts are hand-written per film; no script room | our LLM lanes (DeepSeek/GLM) + a `script.py` that owns word counts and timing |
| 2 | **Look** | a LOOK line first: palette, type, motion grammar, grain, lens | planned — the 6 process gaps were identified; no look file yet | a `look.json` per project + HyperFrames registry blocks |
| 3 | **Storyboard** | frame-accurate shot list with durations that sum to the target | built — `films/<id>.json` beats with durations, storyboard cards in the UI | this plugin |
| 4 | **Assets in** | capture the product, screenshots, screen recordings, stock, generated stills | **built today** — intake with upload, drag-drop, proxy, thumbnail, sidecar; stills → moving clips; recordings → trims | `assets_api.py` + ffmpeg |
| 5 | **Image generation** | keyframes and product shots from a text prompt | planned | ComfyUI on the fleet (SDXL present; H3 expects a start frame) |
| 6 | **Video generation** | image→video, text→video, 5–10 s per shot, seeded and re-rolled | **client built, engine offline** — `comfy.py` submits/waits/downloads, workflow by node title | MiniMax H3 (`minimax_h3_fl2va_*.safetensors`) and Wan 2.1 I2V on sparke |
| 7 | **Character / influencer** | consistent face across shots; lip-sync to the VO | planned — needs a face-id path and a lip-sync node | InstantID/LoRA + a lip-sync node in the same ComfyUI graph |
| 8 | **Music** | a bed that matches the cut, not a stock loop | partial — procedural bed (`tools/audio.py`), Music Studio plugin exists separately | MiniMax Music 3 weights are on sparke; bridge to ComfyUI |
| 9 | **VO / TTS** | one take, split on silence, per-beat timing | built earlier — `vo_split.py`, `--expect N` | local TTS |
| 10 | **Sound design** | a tick on every transition, a riser before the payoff | planned | ComfyUI audio or ffmpeg-synthesised hits, placed by cut times |
| 11 | **Edit** | tracks, trims, ripple, transitions, keyframes, undo | **engine built** — MLT project model + `melt`; timeline UI not yet | `mlt_project.py`, then `ops.py` + canvas timeline |
| 12 | **Mix** | −16 LUFS, ducking under VO, dips at cuts | built | `tools/audio.py` |
| 13 | **Captions** | burned-in, legible at 34 px+, no line longer than ~7 words | partial — captions exist in the cutting room, not in Films | ffmpeg `subtitles` or HyperFrames text |
| 14 | **Delivery** | 16:9 for the site, 9:16 for shorts, thumbnail, −16 LUFS, correct bitrate | planned — one aspect ratio today | one MLT profile per aspect ratio, same timeline |
| 15 | **QC** | hook in 2 s, motion every second, no dead air, legible type, loudness | built — 9 gate checks + hook/motion/legibility | `tools/gate.py` |
| 16 | **Package** | a folder of exports + the project file, so it can be redone | partial — AllShare folder exists; OTIO export now possible | this plugin |

## 2. What "fully featured" means for the plugin, concretely

**Rooms** (the app already has a rail with rooms — Films, Assets, Generate):

1. **Films** — storyboard, per-shot render, gate. *built*
2. **Assets** — intake, upload, preview, still→clip, trim. *built today*
3. **Generate** — GPU status, model availability, workflow queue. *status built, engine offline*
4. **Edit** — timeline with track clips, trims, transitions, undo. *next*
5. **Mix** — loudness, ducking, dips. *exists as a tool, needs a room*
6. **Deliver** — aspect ratios, burn-in captions, export presets. *planned*

**The rule that keeps it professional:** every stage leaves a file, and every file is
inspectable. A beat is a JSON entry, a shot is an HTML composition, a still is a clip, a cut is
an MLT XML, a project is OTIO. Nothing lives only inside a UI.

## 3. What must be true before it can be called fully featured

1. A **script room**: paste an idea, get N beats with spoken lines and durations that sum to the
   target length, editable per line. Without it, every film starts from hand-written HTML.
2. **Generation wired to the GPUs**: ComfyUI started on sparke, a H3 workflow file, and a beat
   that says `"renderer": "comfy"` rendering the shot and dropping it into `renders/shots/`.
3. **Timeline with undo** (Phase A+B of the editing plan) so a cut can be revised without
   regenerating shots.
4. **Audio post in the UI**: duck, fade, LUFS readout, and a dip at every cut.
5. **Delivery presets**: 16:9 / 9:16 / 1:1 from one timeline, captions burned, thumbnail frame.
6. **A look file** so the films stop looking like default-Inter-on-black.

## 4. Order that keeps the deadline safe

| Order | Work | Why this order |
|---|---|---|
| 1 | ComfyUI up on sparke + one H3 workflow rendering a beat | the models are the reason the studio exists; prove the pipe |
| 2 | Script room (beats from an idea) | removes hand-written HTML from the critical path |
| 3 | Timeline + undo (editing plan Phase A/B) | turns renders into an edit |
| 4 | Delivery presets + captions | makes one film usable on three surfaces |
| 5 | Look file + registry blocks | the taste layer, once the pipe is trustworthy |

## 5. Honest limits

- **H3 is 5–10 s per shot.** A 45 s film is 6–9 generated shots, each with its own seed. Re-rolls
  are part of the workflow, not a failure.
- **A GB10 is not an H100.** Expect minutes per shot, not seconds; the plan's Phase 3 benchmark
  (3 s clip under 3 min) is the gate for calling it usable.
- **ComfyUI graphs are not portable.** A workflow file contains model filenames; a beat must
  declare which model family it expects so a missing model is an error, not a silent fallback.
- **Nothing here replaces taste.** The gate measures motion, legibility and loudness. The only
  real test of a film is still a person watching the first two seconds.
