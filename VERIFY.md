# Verify

Every claim, and the command that falsifies it.

```bash
cd ~/Development/video-studio

# 1. the engine's own claims (9 checks, builds its own fixture)
python3 tests/run_tests.py

# 2. the plugin is a legal Hermes plugin, and registers exactly one tool
hermes plugins validate .
hermes plugins doctor video-studio

# 3. it accepts and rejects real footage
python3 -m video-studio cli analyze   demo/before.mp4      # silences, scenes, suggested cuts
python3 -m video-studio cli new       demo/before.mp4 /tmp/demo.json
python3 -m video-studio cli tighten   /tmp/demo.json       # prints the new duration
python3 -m video-studio cli render    /tmp/demo.json /tmp/demo.mp4
python3 -m video-studio cli verify    /tmp/demo.mp4        # exit 1 if not shippable

# 4. the gate actually fails things (a 5 s cap on an 8 s file)
python3 -c "import video_studio.edl as e; p=e.Project('demo/before.mp4'); \
print(p.verify('demo/before.mp4', max_seconds=5)['failures'])"
```

Expected: 9/9 pass, validation passes, `verify` reports a failure list rather
than an opinion.

## Films (HyperFrames room)

```bash
python3 - <<'PY'
import json, urllib.request
base = "http://127.0.0.1:8756/api/plugins/video-studio"
def get(path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)
films = get("/films")
print("films:", [f["id"] for f in films["films"]], "workspace:", films["workspace"])
gate = get("/films/aura/gate", "POST")
print("gate:", gate["shippable"], [(c["check"], c["pass"]) for c in gate["checks"]])
job = get("/films/aura/beats/shot-aura-hero/render", "POST")
print("shot render job:", job["job"])
PY
```
Expected: `films: ['aura', 'gameshelf']`; `gate: True` with `hook`, `motion`, `legibility`
all passing; a job id for the single-shot render (~31 s for a 7 s shot).
