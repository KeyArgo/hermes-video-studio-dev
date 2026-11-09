# Implementation plan — Video Studio, remaining work

Repo: `~/Development/hewn-video-studio` · written 2026-11-08 · **no code executed in this plan**
Strategy doc (why / who / deadline): `~/Vaults/hackathons/2026-11-08-video-studio-plan.md`

State at time of writing: engine + store + service + API + tab + standalone server
working; `tests/run_tests.py` 10/10; `tests/test_cards.py` 4/4. Installed and enabled
as `hewn-video-studio`.

---

## WP1b — a card is an op, not a special case

The only risky change left, because it touches the one code path every render uses.

**Design decision (settle before touching code):** a timeline segment gains a `kind`:

```python
{"kind": "src",  "src_in": 0.0, "src_out": 12.4, "speed": 1.0, "muted": False}
{"kind": "card", "png": "cards/title-01.png", "duration": 1.2}
```

Cards are *not* source ranges. Treating them as fake footage would make every
later op (`snip`, `speed`, `tighten`) try to reason about seconds that do not
exist in the source. `snip` over a card segment removes the whole card; `speed`
on a card is a no-op with a warning; `tighten` never measures cards.

Files and order:

1. `video_studio/edl.py`
   - `timeline()` emits `kind` on every segment; card segments carry `png` + `duration`.
   - `_slice` / `_keep` / `_retime` / `_mute` skip card segments (cards are atomic:
     in or out, never trimmed).
   - `render()` builds one input per distinct card PNG (`-loop 1 -i png`), adds a
     `-f lavfi anullsrc` audio label per card, and concats `kind`-aware labels.
2. `video_studio/service.py`
   - `apply_op("card", kind=..., at=..., seconds=..., title=...)` → renders the PNG via
     `cards.render_card` into `<project>/cards/`, then appends
     `{"op": "card", "kind": ..., "png": ..., "at": ..., "seconds": ...}`.
   - `at` is a timeline position; the op splices the card in at that point.
3. `__init__.py` — add `card` to the tool's action enum + schema (`kind`, `at`, `seconds`).
4. `dashboard/dist/index.js` — card segments render in the timeline bar with a distinct
   fill and a `C` glyph; no editing UI for cards beyond delete-by-snip.

**Accept (I run these):**
- `python3 tests/run_tests.py` still 10/10 (no regression in src-only projects)
- new test: title card + 2 src segments + end card → duration = sum, gate passes,
  first-5s motion check still passes
- `python3 tests/test_cards.py` 4/4
- render a real 12s cut with cards at both ends and watch it

**Do not:** give cards a `src_in/src_out`, or add a second render path for them.

---

## WP2 — captions from the UI

1. `service.apply_op("caption", ...)` already exists; the UI needs a range → text flow.
2. `dashboard/dist/index.js`: selection + `C` key or a button opens an inline mono input
   capped at 7 words; caption appears as a band on the waveform at its timeline offset.
3. Correctness item (from the GLM review): captions do not shift when a later snip
   changes the timeline. Either remap caption times when an op changes earlier
   durations, or refuse with a clear message. **Prefer remapping** — silence here is
   the footgun.
4. `LIMITS.md` entry only if remapping is not done.

**Accept:** add caption at 10–14s, snip 2–4s before it, caption still lands on the same
words (verified by extracting a frame at the new offset), 8-word caption rejected.

---

## WP3 — write the accepted footguns into LIMITS.md

Three items, from lane review, all by design and none of them bugs:
- speed/trim coordinates are per-op against the timeline as it stood then
- mute splits segments and mutes the inside piece (partial mute is real)
- undo pops one op; there is no redo stack

**Accept:** each has one sentence + one failing example a reader can reproduce.

---

## WP5 — Music Studio handoff (the "they work as a team" story)

1. `video_studio/service.py`: `new(..., origin={"studio":"music","take":id,"seed":n})`
   stored in `meta.origin`; shown in the tab header when present.
2. Shared approval rule: `POST /projects/{id}/approve` is the only way a cut becomes
   postable; the agent tool **cannot** call it. Render output gains `approved: false`.
3. Player: when a take's audio is present, show `origin` in the clip facts.

**Accept:** import a Music Studio take path, origin visible in the tab, agent render
returns `approved: false`, human approve flips it; the tool rejects `action=approve`.

---

## WP6 — the studio's own 90s video

1. Footage: this studio cutting one of the seven demo clips (real work, real latency).
2. Money shot (first 5s): waveform drag → the cut jumps shorter → gate turns green.
3. Honest limit: one video track, no transitions, no ASR.
4. Record against a tagged commit; run `hewn-demo-kit/qa.py` (9/9) before posting.

---

## Order, and what gets cut

`WP1b → WP2 → WP3 → WP6 → WP5`. If time dies: drop WP5, then WP2's remapping
(documents instead), never WP1b or WP6.

## Open decisions I cannot make alone

1. **spark A/B restart** — pair is unreachable; `./start.sh restart` on A is a hardware
   action that needs an explicit yes.
2. **Studio as entry #8** — yes adds WP6 (~2h); no makes this tooling only.
