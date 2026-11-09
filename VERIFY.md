# Verify

Every claim, and the command that falsifies it.

```bash
cd ~/Development/hewn-cut

# 1. the engine's own claims (9 checks, builds its own fixture)
python3 tests/run_tests.py

# 2. the plugin is a legal Hermes plugin, and registers exactly one tool
hermes plugins validate .
hermes plugins doctor hewn-cut

# 3. it accepts and rejects real footage
python3 -m hewn_cut.cli analyze   demo/before.mp4      # silences, scenes, suggested cuts
python3 -m hewn_cut.cli new       demo/before.mp4 /tmp/demo.json
python3 -m hewn_cut.cli tighten   /tmp/demo.json       # prints the new duration
python3 -m hewn_cut.cli render    /tmp/demo.json /tmp/demo.mp4
python3 -m hewn_cut.cli verify    /tmp/demo.mp4        # exit 1 if not shippable

# 4. the gate actually fails things (a 5 s cap on an 8 s file)
python3 -c "import hewn_cut.edl as e; p=e.Project('demo/before.mp4'); \
print(p.verify('demo/before.mp4', max_seconds=5)['failures'])"
```

Expected: 9/9 pass, validation passes, `verify` reports a failure list rather
than an opinion.

## Films (HyperFrames room)

```bash
curl -s localhost:8756/api/plugins/hewn-video-studio/films | python3 -m json.tool | head
```
Expected: `{"films": [{"id": "aura", ...}, {"id": "gameshelf", ...}], "workspace": "/home/dev/Development/hewn-motion"}`

```bash
curl -s -X POST localhost:8756/api/plugins/hewn-video-studio/films/aura/gate | python3 -m json.tool
```
Expected: `"shippable": true` with three checks (`hook`, `motion`, `legibility`), each `"pass": true`.

```bash
curl -s -X POST localhost:8756/api/plugins/hewn-video-studio/films/aura/beats/shot-aura-hero/render
```
Expected: `{"job": "<id>", "output": "../renders/shots/shot-aura-hero.mp4"}`; poll
`/jobs/<id>` until `"state": "done"` (~31 s for a 7 s shot at draft quality).
