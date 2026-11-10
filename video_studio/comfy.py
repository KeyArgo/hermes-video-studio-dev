"""ComfyUI as a renderer for beats: queue a workflow, wait, take the file.

The models people actually use for AI video live on a ComfyUI instance, not in this plugin:
MiniMax H3 (image/text -> video with audio), Wan 2.1 image-to-video, and the VAEs they need.
This module is the only place that talks to ComfyUI, so "is the GPU ready?" has one answer.

Design rules:
  * a beat's workflow is a JSON file in the film workspace, so an author can drop in a graph
    made in the ComfyUI UI without touching Python;
  * parameters (prompt, image, seconds, seed) are substituted into that graph by node title,
    never by hard-coded node ids, because ids change every time someone re-edits a graph;
  * the client reports *why* it cannot render — no queue, no model, no GPU — instead of
    failing with a stack trace.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_HOST = "http://10.0.0.52:8188"

# Model names as they exist on the fleet (surveyed from ~/AI/comfyui-models on sparke).
KNOWN_MODELS = {
    "minimax_h3": {
        "label": "MiniMax H3 (image+text → video with audio)",
        "diffusion_models": ["minimax_h3_fl2va_pruned_fp8_scaled.safetensors",
                             "minimax_h3_fl2va_pruned_int8_convrot.safetensors"],
        "vae": ["minimax_h3_video_vae_fp16.safetensors"],
        "audio_vae": ["minimax_h3_audio_vae_fp32.safetensors"],
        "typical_seconds": 6.0,
    },
    "wan_i2v": {
        "label": "Wan 2.1 image → video (480p, 14B)",
        "diffusion_models": ["Wan2_1-I2V-14B-480p_fp8_e4m3fn_scaled_KJ.safetensors"],
        "vae": [],
        "typical_seconds": 4.0,
    },
    "minimax_music": {
        "label": "MiniMax Music 3 (music from text)",
        "diffusion_models": ["minimax_music3_dit_int8_convrot.safetensors"],
        "vae": ["minimax_music3_dav.safetensors"],
        "typical_seconds": 30.0,
    },
}


class ComfyError(RuntimeError):
    pass


class Comfy:
    """Minimal client: submit a prompt graph, poll history, download the outputs."""

    def __init__(self, host: str | None = None, timeout: float = 10.0):
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.timeout = timeout

    # ---------------------------------------------------------------- transport
    def _request(self, path: str, payload: dict | None = None, raw: bool = False,
                 timeout: float | None = None):
        url = f"{self.host}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                return body if raw else json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            raise ComfyError(f"{path} -> HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyError(f"{self.host} unreachable: {exc}") from None

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        """What this instance can do right now — answered, not assumed."""
        try:
            stats = self._request("/system_stats", timeout=4)
            queue = self._request("/queue", timeout=4)
        except ComfyError as exc:
            return {"ok": False, "host": self.host, "reason": str(exc)}
        devices = stats.get("devices") or []
        models = {}
        for name in ("UNETLoader", "CheckpointLoaderSimple"):
            try:
                payload = self._request(f"/object_info/{name}", timeout=6)
            except ComfyError:
                payload = None
            # /object_info/<node> answers {node: {input: ...}}; some builds answer the inner dict
            if isinstance(payload, dict) and isinstance(payload.get(name), dict):
                payload = payload[name]
            models[name] = payload
        available = []
        for key, spec in KNOWN_MODELS.items():
            present = []
            for wanted in spec["diffusion_models"]:
                for loader in models.values():
                    if not loader:
                        continue
                    options = (loader.get("input", {}).get("required", {})
                               .get("unet_name", {}) or loader.get("input", {})
                               .get("required", {}).get("ckpt_name", {}))
                    choices = _choices(options)
                    # ComfyUI lists files by their path under models/, so match on the tail
                    if any(c == wanted or c.endswith("/" + wanted) for c in choices):
                        present.append(wanted)
                        break
            available.append({"id": key, "label": spec["label"], "installed": present,
                              "ready": bool(present)})
        return {"ok": True, "host": self.host,
                "devices": [{"name": d.get("name"), "vram_total": d.get("vram_total"),
                             "vram_free": d.get("vram_free")} for d in devices],
                "queue": {"running": len(queue.get("queue_running") or []),
                          "pending": len(queue.get("queue_pending") or [])},
                "models": available}

    # ------------------------------------------------------------------- graph
    def submit(self, graph: dict, params: dict | None = None) -> str:
        """Queue a workflow; ``params`` are substituted into nodes by their ``_meta.title``."""
        prepared = substitute(graph, params or {})
        client_id = f"video-studio-{int(time.time())}"
        answer = self._request("/prompt", {"prompt": prepared, "client_id": client_id},
                               timeout=30)
        prompt_id = answer.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"prompt rejected: {json.dumps(answer)[:300]}")
        return prompt_id

    def wait(self, prompt_id: str, timeout: float = 900.0, poll: float = 2.0) -> dict:
        """Block until the prompt finishes, then return its history entry."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            history = self._request(f"/history/{prompt_id}", timeout=15)
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    raise ComfyError(f"workflow failed: {json.dumps(status)[:400]}")
                return entry
            time.sleep(poll)
        raise ComfyError(f"timed out after {timeout:.0f}s waiting on {prompt_id}")

    def outputs(self, entry: dict) -> list[dict]:
        """Every produced file, as {filename, subfolder, kind}."""
        found = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for kind in ("images", "gifs", "videos", "audio"):
                for item in out.get(kind) or []:
                    found.append({"node": node_id, "kind": kind, "filename": item.get("filename"),
                                  "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
        return found

    def download(self, item: dict, dest: Path) -> Path:
        query = (f"/view?filename={urllib.parse.quote(str(item['filename']))}"
                 f"&subfolder={urllib.parse.quote(item.get('subfolder') or '')}"
                 f"&type={item.get('type') or 'output'}")
        body = self._request(query, raw=True, timeout=120)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest

    def render(self, graph: dict, params: dict, dest: Path, timeout: float = 900.0) -> dict:
        started = time.time()
        prompt_id = self.submit(graph, params)
        entry = self.wait(prompt_id, timeout=timeout)
        files = self.outputs(entry)
        if not files:
            raise ComfyError("workflow produced no files")
        dest = Path(dest)
        written = []
        for i, item in enumerate(files):
            target = dest if i == 0 else dest.with_name(f"{dest.stem}-{i}{dest.suffix}")
            written.append(str(self.download(item, target)))
        return {"prompt_id": prompt_id, "files": written, "seconds": round(time.time() - started, 1)}


def _choices(options) -> list[str]:
    """ComfyUI object_info option shapes vary: [list, {...}] or {choices: [...]}."""
    if isinstance(options, list) and options:
        first = options[0]
        if isinstance(first, list):
            return [str(x) for x in first]
    if isinstance(options, dict):
        return [str(x) for x in (options.get("choices") or [])]
    return []


def substitute(graph: dict, params: dict) -> dict:
    """Replace values in nodes whose ``_meta.title`` is a key in ``params``.

    Also accepts params keyed by node id, for graphs made by hand. Inputs of type int/float
    accept a string and are cast, because prompts come from a UI.
    """
    clone = json.loads(json.dumps(graph))
    for node_id, node in clone.items():
        title = (node.get("_meta") or {}).get("title")
        payload = params.get(title) if title in params else params.get(node_id)
        if payload is None:
            continue
        for key, value in payload.items():
            current = node.get("inputs", {}).get(key)
            if isinstance(current, int) and isinstance(value, str) and value.isdigit():
                value = int(value)
            elif isinstance(current, float) and isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    pass
            node.setdefault("inputs", {})[key] = value
    return clone


def load_graph(path: Path) -> dict:
    """A workflow saved from the ComfyUI UI ('Save (API Format)')."""
    data = json.loads(Path(path).read_text())
    graph = data.get("prompt") if isinstance(data, dict) and "prompt" in data else data
    if not isinstance(graph, dict) or not graph:
        raise ComfyError(f"{path} is not an API-format workflow")
    return graph
