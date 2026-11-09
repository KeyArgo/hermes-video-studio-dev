# Plan — GPU generation bus + editing-suite granularity

Saved 2026-11-09 00:46 · workspace: `~/Development/hewn-video-studio`
Status: planning only, nothing here is implemented.

## Goal

One way to point any studio at a GPU on the network — ComfyUI on a Spark running MiniMax Music 3, MiniMax Hailuo/H3 video, or a fallback video model — with every generated asset carrying its full recipe, and every asset editable at clip granularity in Video Studio.

## Current context (verified 2026-11-09, not assumed)

**What already exists and works**

| Piece | Where | State |
|---|---|---|
| Engine contract + registry with honest fallback | `~/.hermes/plugins/hermes-music-studio/music_studio/engines/{contract,registry}.py` | working, tested |
| ComfyUI client for MiniMax Music 3 | `.../engines/minimax_m3_comfy.py` | submits graph → polls `/history` → fetches `/view`; `DEFAULT_URL = http://10.0.0.52:8188` |
| Video cut engine (ops replay, gate) | `~/Development/hewn-video-studio/video_studio/edl.py` | 15/15 + 6/6 + 4/4 tests |
| Video Studio UI + API + agent tool | `dashboard/`, `service.py`, `__init__.py` | running on `:8756` |

**GPU reality — this is the constraint that shapes everything**

| Host | GPU | Currently leased by | ComfyUI on disk? |
|---|---|---|---|
| sparka `10.0.0.50` | GB10 | DeepSeek-V4.1 tp=2 (container `dsv41-1017-exl3-tf`) | **yes** — `~/ComfyUI` + `comfy-shim-venv` |
| sparkb `10.0.0.51` | GB10 | same tp=2 pair (rank 2) | no |
| sparkc `10.0.0.20` | GB10 | GLM-5.3 (`glm53-spark`) | no |
| sparkd `10.0.0.21` | GB10 | GLM-5.3 (rank 2) | no |
| sparke `10.0.0.52` | GB10 | Qwen3.8 vLLM (`qwen38-flash-next-tf`) | **yes** — `~/spark-comfyui`, `comfy-music3.log` |

Every GPU is already leased by an LLM lane. `http://10.0.0.52:8188` returns `000`: the ComfyUI server that MiniMax Music 3 expects **is not running**.

**Two existing rules to respect, from the fleet notes**

- `sparke` has `earlyoom` configured to SIGTERM large loads; MiniMax-Music3 previously died every ~2 min on E during model load.
- Nothing here may stop or restart LLM containers without an explicit human yes. A plan that "just kills the vLLM" is a plan that breaks the fleet mid-hackathon.

## Architecture

**Three layers, no new abstraction where one exists:**

1. **`gpu_bus`** (new package, `~/Development/hewn-gpu-bus/gpu_bus/`) — a transport-agnostic client for one thing: *run a parameterised ComfyUI workflow somewhere and bring back the file*. It mirrors Music Studio's existing semantics (honest health, no mocks, no silent fallback across engines) but is usable by any studio.
2. **Asset contract** (`asset.json`) — the generated file plus its complete recipe. Music Studio's `take.json` is today's format; the new schema is a superset so a music take is a valid asset with `kind: "audio"`.
3. **Studios consume assets** — Music Studio keeps its engine registry and gains nothing visible (it already works); Video Studio gains `generate` (new clip from a prompt) and `regenerate` (redo one clip, keep the cut). Granular control lives in a `params` object that flows from UI → tool → workflow template unaltered.

Key invariant: **the agent may request a generation and read its measured result; only a human may approve a take for posting, and only a human may grant a GPU lease that displaces an LLM.** That is the same approval rule the two studios already share.

## Step-by-step tasks

Each task is deliberately small. Commit after each green test.

### Task 1 — create the package skeleton

```bash
mkdir -p ~/Development/hewn-gpu-bus/gpu_bus/workflows ~/Development/hewn-gpu-bus/tests
cd ~/Development/hewn-gpu-bus && git init -q && touch gpu_bus/__init__.py tests/__init__.py
```

Expected: `ls gpu_bus` shows `__init__.py workflows`.

### Task 2 — endpoint registry (TDD)

File: `~/Development/hewn-gpu-bus/gpu_bus/endpoints.py`

```python
"""Known GPU hosts. Availability is measured, never assumed."""
import json, os, urllib.request

ENDPOINTS = {
    "sparka": {"url": "http://10.0.0.50:8188", "ssh": "argo@sparka", "gpu": "GB10", "llm": "DeepSeek-V4.1 tp=2"},
    "sparke": {"url": "http://10.0.0.52:8188", "ssh": "argo@sparke", "gpu": "GB10", "llm": "Qwen3.8 vLLM"},
}

def get(name: str) -> dict:
    if name not in ENDPOINTS:
        raise ValueError(f"unknown endpoint {name!r}; have {sorted(ENDPOINTS)}")
    return {"name": name, **ENDPOINTS[name]}

def probe(name: str, timeout: float = 3.0) -> dict:
    """Ask the ComfyUI host itself. Returns {up, vram_free_gb?, error?}."""
    ep = get(name)
    try:
        with urllib.request.urlopen(ep["url"] + "/system_stats", timeout=timeout) as r:
            d = json.loads(r.read())
        dev = (d.get("devices") or [{}])[0]
        return {"name": name, "up": True, "url": ep["url"],
                "vram_free_gb": round(float(dev.get("vram_free", 0)) / 1e9, 1),
                "vram_total_gb": round(float(dev.get("vram_total", 0)) / 1e9, 1)}
    except Exception as exc:
        return {"name": name, "up": False, "url": ep["url"], "error": f"{type(exc).__name__}: {exc}"}
```

Test first — `tests/test_endpoints.py`:

```python
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gpu_bus import endpoints

def test_unknown_endpoint_raises():
    try:
        endpoints.get("nope")
    except ValueError:
        return
    raise AssertionError("unknown endpoint accepted")

def test_probe_reports_down_not_raises():
    r = endpoints.probe("sparke", timeout=1.0)
    assert r["up"] is False, r          # 8188 is closed today
    assert "error" in r

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
```

Run: `cd ~/Development/hewn-gpu-bus && python3 tests/test_endpoints.py`
Expected today: `PASS test_unknown_endpoint_raises`, `PASS test_probe_reports_down_not_raises` (port is closed — that is the correct answer, not a failure to hide).

### Task 3 — ComfyUI client (TDD with a fake)

File: `~/Development/hewn-gpu-bus/gpu_bus/comfy.py`

```python
"""Minimal ComfyUI client: one workflow in, one file out.

Deliberately smaller than Music Studio's engine: no opinion about prompts, models
or licensing. Callers pass a workflow dict that is already complete.
"""
import json, time, urllib.error, urllib.request, uuid


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, base_url, timeout=10.0, poll=1.0, max_wait=1800.0):
        self.base_url = base_url.rstrip("/")
        self.timeout, self.poll, self.max_wait = timeout, poll, max_wait

    def _json(self, path, payload=None):
        url = self.base_url + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read() or b"{}")

    def submit(self, workflow: dict) -> str:
        res = self._json("/prompt", {"prompt": workflow,
                                     "client_id": uuid.uuid4().hex})
        if "prompt_id" not in res:
            raise ComfyError(f"submit refused: {str(res)[:300]}")
        return res["prompt_id"]

    def wait(self, prompt_id: str) -> list:
        """Poll /history until the prompt appears. Returns its output files."""
        deadline = time.time() + self.max_wait
        while time.time() < deadline:
            try:
                hist = self._json(f"/history/{prompt_id}")
            except urllib.error.URLError:
                time.sleep(self.poll); continue      # transient; keep polling
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = (entry.get("status") or {}).get("status_str")
                if status and status != "success":
                    raise ComfyError(f"workflow {status}: {str(entry)[:300]}")
                out = []
                for node in (entry.get("outputs") or {}).values():
                    for key in ("audio", "gifs", "images", "videos"):
                        out.extend(node.get(key) or [])
                return out
            time.sleep(self.poll)
        raise ComfyError(f"timeout after {self.max_wait:.0f}s waiting for {prompt_id}")

    def fetch(self, ref: dict, out_path: str) -> str:
        q = (f"/view?filename={urllib.parse.quote(ref['filename'])}"
             f"&subfolder={urllib.parse.quote(ref.get('subfolder',''))}"
             f"&type={ref.get('type','output')}")
        with urllib.request.urlopen(self.base_url + q, timeout=self.timeout) as r, \
                open(out_path, "wb") as f:
            f.write(r.read())
        return out_path
```

Add `import urllib.parse` at the top of the file.

Test with a fake server — `tests/fake_comfy.py`:

```python
"""Tiny stand-in for ComfyUI: /prompt, /history/<id>, /view."""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class FakeComfy:
    def __init__(self, payload=b"RIFFfake", fail=False):
        self.payload, self.fail, self.prompts = payload, fail, {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                pid = "p1"
                outer.prompts[pid] = body.get("prompt")
                self._send({"prompt_id": pid})
            def do_GET(self):
                if self.path.startswith("/history/"):
                    if outer.fail:
                        return self._send({"p1": {"status": {"status_str": "error"}}})
                    return self._send({"p1": {"status": {"status_str": "success"},
                                              "outputs": {"9": {"audio": [
                                                  {"filename": "t.flac", "subfolder": "", "type": "output"}]}}}})
                if self.path.startswith("/view"):
                    self.send_response(200); self.send_header("Content-Length", str(len(outer.payload)))
                    self.end_headers(); self.wfile.write(outer.payload); return
                self._send({})
            def _send(self, obj):
                raw = json.dumps(obj).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw))); self.end_headers()
                self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start(); return self

    def __exit__(self, *a):
        self.httpd.shutdown()
```

Test — `tests/test_comfy.py`:

```python
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gpu_bus.comfy import Comfy, ComfyError
from tests.fake_comfy import FakeComfy

def test_roundtrip():
    with FakeComfy() as f:
        c = Comfy(f.url, poll=0.05, max_wait=5)
        pid = c.submit({"1": {"class_type": "X", "inputs": {}}})
        files = c.wait(pid)
        assert files and files[0]["filename"] == "t.flac", files
        out = c.fetch(files[0], os.path.join(tempfile.mkdtemp(), "t.flac"))
        assert os.path.getsize(out) == 8

def test_error_status_raises():
    with FakeComfy(fail=True) as f:
        c = Comfy(f.url, poll=0.05, max_wait=5)
        try:
            c.wait(c.submit({"1": {}}))
        except ComfyError as exc:
            assert "error" in str(exc); return
        raise AssertionError("error status swallowed")
```

Run: `cd ~/Development/hewn-gpu-bus && python3 tests/test_comfy.py`
Expected: two `PASS` lines.

### Task 4 — discover what video model is actually installable on sparka

Read-only inspection, then a written finding (no code):

```bash
ssh argo@sparka 'ls ~/ComfyUI/custom_nodes; ls ~/ComfyUI/models/diffusion_models 2>/dev/null | head; du -sh ~/ComfyUI 2>/dev/null'
ssh argo@sparke 'tail -40 ~/comfy-music3.log 2>/dev/null'
```

Record in `PLAN-NOTES.md`: which node packs exist (`nodes_minimax_music`, any Hailuo/Wan/LTX video pack), and therefore **which** video model Task 5 targets. If MiniMax Hailuo/H3 nodes are absent, Task 5 targets a model the host already supports (Wan 2.x / LTX-Video) and the plan records H3 as a licence+download task, not a blocker.

### Task 5 — parameterised workflow templates

File: `~/Development/hewn-gpu-bus/gpu_bus/workflows/loader.py`

```python
"""Workflow templates are JSON with {{placeholders}}; fill, do not generate."""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))

def load(name: str) -> dict:
    path = os.path.join(HERE, f"{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no workflow template {name!r} in {HERE}")
    return json.load(open(path))

def _sub(value, params):
    if isinstance(value, str):
        def rep(m):
            key = m.group(1).strip()
            if key not in params:
                raise KeyError(f"workflow needs parameter {key!r}; got {sorted(params)}")
            return str(params[key])
        out = re.sub(r"\{\{([^}]+)\}\}", rep, value)
        # numbers stay numbers so ComfyUI type-checks them
        try:
            return int(out) if re.fullmatch(r"-?\d+", out) else float(out)
        except ValueError:
            return out
    if isinstance(value, list):
        return [_sub(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _sub(v, params) for k, v in value.items()}
    return value

def build(name: str, params: dict) -> dict:
    return _sub(load(name), params)
```

Test `tests/test_workflows.py`: build a tiny template written to a temp dir with `{"seed": "{{seed}}", "steps": "{{steps}}"}` and assert types are `int`; assert a missing parameter raises `KeyError` naming it.

Run: `python3 tests/test_workflows.py` → `PASS subst keeps ints`, `PASS missing parameter is named`.

### Task 6 — asset contract (the recipe travels with the file)

File: `~/Development/hewn-gpu-bus/gpu_bus/assets.py`

```python
"""asset.json — the generated file plus everything needed to make it again."""
import json, os, time, uuid

SCHEMA = 1

def new(kind, path, *, provider, model, params, seed=None, parent=None,
        workflow=None, licence=None, latency_s=None, endpoint=None) -> dict:
    if kind not in ("audio", "video", "image"):
        raise ValueError(f"kind must be audio|video|image, got {kind!r}")
    return {
        "schema": SCHEMA, "id": uuid.uuid4().hex[:12], "kind": kind,
        "path": os.path.abspath(path), "bytes": os.path.getsize(path),
        "created": int(time.time()), "provider": provider, "endpoint": endpoint,
        "model": model, "workflow": workflow, "params": dict(params or {}),
        "seed": seed, "parent": parent, "licence": licence, "latency_s": latency_s,
    }

def write(asset: dict, directory: str) -> str:
    os.makedirs(directory, exist_ok=True)
    p = os.path.join(directory, f"{asset['id']}.asset.json")
    with open(p, "w") as f:
        json.dump(asset, f, indent=2)
    return p

def read(path: str) -> dict:
    with open(path) as f:
        a = json.load(f)
    if a.get("schema") != SCHEMA:
        raise ValueError(f"unsupported asset schema {a.get('schema')}")
    if not os.path.exists(a["path"]):
        raise FileNotFoundError(f"asset media is gone: {a['path']}")
    return a

def same_recipe(a: dict, b: dict) -> bool:
    """Two assets are a re-render pair when model, workflow and params agree."""
    return (a["model"], a["workflow"], a["params"]) == (b["model"], b["workflow"], b["params"])
```

Test `tests/test_assets.py`: create a temp file, `new("video", ...)`, write, read back, assert `read` round-trips and that deleting the media makes `read` raise.

### Task 7 — GPU lease that cannot steal a GPU

File: `~/Development/hewn-gpu-bus/gpu_bus/lease.py`

```python
"""A GPU is leased by a named process; taking one from an LLM needs a human.

There is no automatic stop of a vLLM container anywhere in this file. If the
requested host is busy, `acquire` raises with the evidence and the operator
decides.
"""
import json, os, time

LEASE_DIR = os.path.expanduser("~/.local/state/hewn-gpu-bus/leases")


def _path(name):
    os.makedirs(LEASE_DIR, exist_ok=True)
    return os.path.join(LEASE_DIR, f"{name}.json")


def status(name) -> dict | None:
    p = _path(name)
    if not os.path.exists(p):
        return None
    return json.load(open(p))


def acquire(name, owner, ttl_s=3600, allow_displace=False) -> dict:
    cur = status(name)
    if cur and cur["owner"] != owner and time.time() < cur["expires"]:
        if not allow_displace:
            raise RuntimeError(
                f"{name} is leased by {cur['owner']} until "
                f"{time.strftime('%H:%M', time.localtime(cur['expires']))}; "
                f"displacing it needs allow_displace=True from a human")
    lease = {"endpoint": name, "owner": owner, "acquired": int(time.time()),
             "expires": int(time.time() + ttl_s)}
    json.dump(lease, open(_path(name), "w"))
    return lease


def release(name, owner) -> None:
    cur = status(name)
    if cur and cur["owner"] == owner:
        os.remove(_path(name))
```

Test `tests/test_lease.py`: acquire, second acquire raises `RuntimeError` naming the owner, `allow_displace=True` succeeds, release clears it.

Run: `python3 tests/test_lease.py` → 4 `PASS`.

### Task 8 — generation service (the one entry point both studios call)

File: `~/Development/hewn-gpu-bus/gpu_bus/generate.py`

```python
"""generate() — pick an endpoint, lease it, run a workflow, write the asset."""
import os, time
from . import assets, comfy, endpoints, lease, workflows


def generate(workflow_name, params, *, endpoint, out_path, kind, model,
             provider="comfyui", owner="studio", ttl_s=1800,
             allow_displace=False, licence=None):
    ep = endpoints.get(endpoint)
    health = endpoints.probe(endpoint)
    if not health.get("up"):
        raise RuntimeError(f"{endpoint} ComfyUI is down: {health.get('error')}")
    lease.acquire(endpoint, owner, ttl_s, allow_displace)
    started = time.time()
    try:
        graph = workflows.build(workflow_name, params)
        client = comfy.Comfy(ep["url"])
        prompt_id = client.submit(graph)
        files = client.wait(prompt_id)
        if not files:
            raise comfy.ComfyError("workflow finished with no output file")
        client.fetch(files[0], out_path)
        asset = assets.new(kind, out_path, provider=provider, model=model,
                           params=params, seed=params.get("seed"),
                           workflow=workflow_name, licence=licence,
                           latency_s=round(time.time() - started, 1),
                           endpoint=ep["url"])
        assets.write(asset, os.path.dirname(os.path.abspath(out_path)))
        return asset
    finally:
        lease.release(endpoint, owner)
```

Test: point `generate` at `FakeComfy` by monkeypatching `endpoints.ENDPOINTS["fake"]` to the fake's URL; assert an `asset.json` lands next to the media and `latency_s` is a float. Also assert a down endpoint raises with "is down".

### Task 9 — Video Studio: `generate` and `regenerate` ops

File: `~/Development/hewn-video-studio/video_studio/generate_op.py`

```python
"""A generated clip is just a source file plus its recipe.

Video Studio does not care how a clip was made; it needs the file to be real and
the recipe to travel with it, so a shot can be redone without touching the cut.
"""
import json, os, sys

BUS = os.path.expanduser("~/Development/hewn-gpu-bus")
if BUS not in sys.path:
    sys.path.insert(0, BUS)


def generate_clip(project_dir, prompt, params, endpoint, workflow, model,
                  kind="video", **kw):
    from gpu_bus import generate as bus_generate
    shots = os.path.join(project_dir, "shots")
    os.makedirs(shots, exist_ok=True)
    name = f"gen-{int(params.get('seed', 0))}-{abs(hash(prompt)) % 10**6}.mp4"
    out = os.path.join(shots, name)
    full = {**params, "prompt": prompt}
    asset = bus_generate(workflow_name=workflow, params=full, endpoint=endpoint,
                         out_path=out, kind=kind, model=model, **kw)
    with open(out + ".asset.json", "w") as f:
        json.dump(asset, f, indent=2)
    return out, asset


def regenerate(project_dir, clip_path, **overrides):
    """Re-run the recipe of an existing clip with changed parameters."""
    recipe_path = clip_path + ".asset.json"
    if not os.path.exists(recipe_path):
        raise FileNotFoundError(f"{clip_path} has no recipe; it was not generated here")
    recipe = json.load(open(recipe_path))
    params = {**recipe["params"], **overrides}
    out, asset = generate_clip(project_dir, params.pop("prompt"), params,
                               endpoint=recipe["endpoint"].split("//")[-1].split(":")[0]
                               if False else _endpoint_name(recipe),
                               workflow=recipe["workflow"], model=recipe["model"],
                               licence=recipe.get("licence"))
    asset["parent"] = recipe["id"]
    json.dump(asset, open(out + ".asset.json", "w"), indent=2)
    return out, asset


def _endpoint_name(recipe):
    from gpu_bus import endpoints
    for name, ep in endpoints.ENDPOINTS.items():
        if ep["url"] == recipe["endpoint"]:
            return name
    raise RuntimeError(f"endpoint {recipe['endpoint']} is no longer in the registry")
```

Then in `video_studio/service.py` add:

```python
    elif op == "generate":
        from . import generate_op
        out, asset = generate_op.generate_clip(
            str(store.project_dir(pid)), str(kw["prompt"]), kw.get("params") or {},
            str(kw["endpoint"]), str(kw["workflow"]), str(kw["model"]))
        p = edl.Project(out, f"{p.name} · shot")
        p.ops = []
        p.meta = {**p.meta, "origin": {"studio": "gpu_bus", "asset": asset["id"],
                                       "model": asset["model"], "seed": asset["seed"]}}
        store.save(pid, p)
        return {"applied": "generate", "clip": out, "asset": asset, **snapshot(p)}
```

and in `__init__.py` add `"generate"` to the enum plus `prompt`, `params`, `endpoint`, `workflow`, `model` properties to the tool schema.

**Accept for Task 9:** with `FakeComfy` bound as an endpoint, `POST /projects/{id}/ops {"op":"generate", ...}` returns `asset.model` and the project source becomes the generated file; `GET /projects/{id}` shows `meta.origin.asset`.

### Task 10 — granular controls, one table, three surfaces

Define the parameter surface once in `gpu_bus/params.py` and render it as (a) tool schema, (b) UI form, (c) workflow placeholders:

```python
CONTROLS = {
    "prompt":      {"type": "string",  "label": "what to make"},
    "negative":    {"type": "string",  "label": "avoid", "default": ""},
    "seed":        {"type": "int",     "label": "seed", "default": 0},
    "steps":       {"type": "int",     "label": "steps", "default": 30, "range": (1, 80)},
    "cfg":         {"type": "float",   "label": "guidance", "default": 4.0, "range": (0.0, 15.0)},
    "sampler":     {"type": "choice",  "label": "sampler", "choices": ["euler", "dpmpp_2m", "uni_pc"]},
    "scheduler":   {"type": "choice",  "label": "scheduler", "choices": ["normal", "karras", "beta"]},
    "width":       {"type": "int",     "label": "width", "default": 1280},
    "height":      {"type": "int",     "label": "height", "default": 720},
    "fps":         {"type": "int",     "label": "fps", "default": 30},
    "duration_s":  {"type": "float",   "label": "seconds", "default": 5.0, "range": (1.0, 20.0)},
    "motion":      {"type": "float",   "label": "motion strength", "default": 0.5, "range": (0.0, 1.0)},
    "ref_image":   {"type": "path",    "label": "reference image", "default": ""},
    "ref_strength":{"type": "float",   "label": "reference strength", "default": 0.6, "range": (0.0, 1.0)},
    "batch":       {"type": "int",     "label": "takes per request", "default": 1, "range": (1, 4)},
}
```

Test: `params.validate({"steps": 999})` raises naming the bound; `params.validate({})` returns defaults.

### Task 11 — deployment: ComfyUI on sparka, without touching the LLM

Do **not** stop `dsv41-1017-exl3-tf` as part of this task. Instead:

```bash
# 1. read-only: what is there now
ssh argo@sparka 'du -sh ~/ComfyUI; ls ~/ComfyUI/custom_nodes; cat ~/comfy-shim-venv/pyvenv.cfg'

# 2. start ComfyUI only if the GPU has headroom left after the LLM pair
ssh argo@sparka 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv'
```

Decision rule, recorded in `PLAN-NOTES.md`: if `memory.used / memory.total > 0.85`, **do not start ComfyUI**; raise the lease conflict to the operator and take the "generation window" option (below) instead.

**Generation window (the honest fallback):** for a 30-minute window agreed with the operator, stop the LLM pair, run the generation batch, restart the pair, and re-verify with the fleet's `verify_sparks.py`. That is a human-approved hardware action, written as its own checklist step, never a side effect of a render.

### Task 12 — wire the UI: a Generate panel with real controls

In `dashboard/dist/index.js`, add a `Generate` section to the left rail: prompt textarea, the controls from `params.CONTROLS` rendered as inputs, endpoint dropdown fed by `GET /api/plugins/.../endpoints`, and a `Generate` button that posts `{op:"generate", prompt, params, endpoint, workflow, model}`.

**Accept:** with the fake endpoint registered, clicking Generate inserts a card-free clip whose duration appears in the header and whose `meta.origin` shows in the Clip facts panel.

### Task 13 — one-page operating doc

`~/Development/hewn-gpu-bus/README.md`: endpoint table, the lease rule in one sentence, the generation-window checklist, and the licence notices for each model used (MiniMax Music 3 already carries one in `minimax_m3_comfy.py`).

## Tests / validation

Run in this order; all must be green before the plan is "done":

```bash
cd ~/Development/hewn-gpu-bus && python3 tests/test_endpoints.py && python3 tests/test_comfy.py \
  && python3 tests/test_workflows.py && python3 tests/test_assets.py && python3 tests/test_lease.py
cd ~/Development/hewn-video-studio && python3 tests/run_tests.py && python3 tests/test_card_ops.py
curl -s localhost:8756/api/plugins/hewn-video-studio/health   # {"ok":true,...}
```

Expected: 2 + 2 + 2 + 3 + 4 `PASS` lines, then `15 passed, 0 failed`, then `6/6`, then the health JSON.

The gate still owns quality: any generated clip that enters a cut is measured by `verify()` like any other footage — duration, 1080p, 30 fps, loudness, dead air, motion in the first 5 s.

## Risks, tradeoffs, open questions

1. **Every GPU is leased.** A/B run DeepSeek tp=2, C/D run GLM, E runs Qwen. ComfyUI video needs real VRAM. Options, in order of honesty: (a) generation windows with explicit approval; (b) shrink one LLM (E's Qwen at lower `--gpu-memory-utilization`) — needs your call; (c) add a sixth box. The plan must not silently pick (b).
2. **`earlyoom` on E** killed MiniMax during load before. Large video DiTs will hit this harder. Any long model load needs an earlyoom exception, which is a system change that needs approval.
3. **Model licence.** MiniMax Music 3 requires attribution in a commercial product's UI. A Hailuo/H3 video model likely has its own terms. `asset.json.licence` exists so this cannot get lost.
4. **Workflow templates rot.** A ComfyUI node-pack update can rename inputs. Mitigation: templates are data, and `/object_info` is probed before submit (Task 5 extension) so a mismatch fails loudly instead of producing garbage.
5. **Open question 1:** is "MiniMax H3" Hailuo video, or a newer music model? It decides Task 4's target and Task 5's template.
6. **Open question 2:** which Spark may be interrupted for generation windows — sparka (has ComfyUI on disk, but hosts DeepSeek half) or sparke (has the music3 history, but hosts Qwen)?
7. **YAGNI note:** no multi-user auth, no cloud fallback, no job queue with priorities until a second consumer exists.
```
