"""Video Studio — a non-destructive edit list an agent can operate.

Why this shape and not "a video editor": an agent is bad at judging footage and
worse at holding state. It is good at emitting one small declarative operation
and reading a measured result. So a project is:

    source  +  [op, op, op, ...]

State is `replay(source, ops)`. Nothing is ever baked in, so undo is popping an
op and re-rendering ~2 seconds of thought instead of an hour of work. Every op
carries its own data (no ffmpeg at replay time), so replay is pure and instant.

The claim worth making: the agent cannot claim a cut is good. `render()` returns
measured facts and `verify()` fails loudly — the model has to show you the
numbers, not describe them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

W, H, FPS = 1920, 1080, 30
LOUDNESS = -16.0
FFMPEG = "ffmpeg"      # plugin config (config_schema.ffmpeg) overrides these
FFPROBE = "ffprobe"


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #
def _run(cmd) -> subprocess.CompletedProcess:
    exe = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}.get(cmd[0])
    if exe:
        cmd = [exe, *cmd[1:]]
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: str) -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
              "-of", "json", path])
    if r.returncode != 0:
        raise ValueError(f"cannot read {path}: {r.stderr.strip()[:200]}")
    meta = json.loads(r.stdout)
    v = next((s for s in meta["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in meta["streams"] if s["codec_type"] == "audio"), None)
    num, den = (v["r_frame_rate"].split("/") if v else ("0", "1"))
    return {
        "path": os.path.abspath(path),
        "duration": round(float(meta["format"]["duration"]), 3),
        "width": v["width"] if v else 0,
        "height": v["height"] if v else 0,
        "fps": round(int(num) / int(den), 3) if v and int(den) else 0,
        "video_codec": v["codec_name"] if v else None,
        "audio_codec": a["codec_name"] if a else None,
        "has_audio": bool(a),
        "size_mb": round(int(meta["format"]["size"]) / 1e6, 2),
    }


def _silences(path: str, noise_db: float = -35.0, min_d: float = 0.45):
    r = _run(["ffmpeg", "-hide_banner", "-i", path, "-af",
              f"silencedetect=n={noise_db}dB:d={min_d}", "-f", "null", "-"])
    spans, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            spans.append((round(start, 2), round(float(m.group(1)), 2)))
            start = None
    return spans


def _scenes(path: str, threshold: float = 0.03):
    r = _run(["ffmpeg", "-hide_banner", "-i", path, "-vf",
              f"select='gt(scene,{threshold})',metadata=print", "-an", "-f", "null", "-"])
    return [round(float(x), 2) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def _loudness(path: str):
    r = _run(["ffmpeg", "-hide_banner", "-i", path, "-af",
              "loudnorm=I=-16:TP=-1.5:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        return {}
    d = json.loads(m.group(0))
    return {"integrated_lufs": float(d["input_i"]),
            "true_peak_db": float(d["input_tp"]),
            "lra": float(d["input_lra"])}


def transform_captions(caps, kind, rng=None, factor=None):
    """Shift captions when an op changes the timeline.

    caps: list of {"start": float, "end": float, "text": str} in timeline seconds BEFORE an op.
    kind: "snip" | "trim" | "speed" | "mute"
    rng:  (start, end) tuple for snip/trim/speed/mute.
    factor: float for speed (2.0 = twice as fast).
    Returns a NEW list in timeline seconds AFTER the op.
    """
    def mk(s, e, t):
        return {"start": s, "end": e, "text": t}

    def keep(s, e, t):
        if e - s > 0.01:
            return mk(s, e, t)
        return None

    out = []

    if kind == "mute":
        for c in caps:
            out.append(mk(c["start"], c["end"], c["text"]))
        return out

    if kind == "snip":
        a, b = rng
        d = b - a
        for c in caps:
            s, e, t = c["start"], c["end"], c["text"]
            if e <= a:
                r = keep(s, e, t)
                if r: out.append(r)
            elif s >= b:
                r = keep(s - d, e - d, t)
                if r: out.append(r)
            elif s >= a and e <= b:
                pass
            elif s < a and e > b:
                r1 = keep(s, a, t)
                if r1: out.append(r1)
                r2 = keep(b - d, e - d, t)
                if r2: out.append(r2)
            elif s < a and e <= b:
                r = keep(s, a, t)
                if r: out.append(r)
            elif s >= a and e > b:
                r = keep(b - d, e - d, t)
                if r: out.append(r)
        return out

    if kind == "trim":
        a, b = rng
        for c in caps:
            s, e, t = c["start"], c["end"], c["text"]
            ns = max(s, a)
            ne = min(e, b)
            if ne > ns:
                r = keep(ns - a, ne - a, t)
                if r: out.append(r)
        return out

    if kind == "speed":
        a, b = rng
        f = factor
        d = b - a
        shift = d - d / f
        for c in caps:
            s, e, t = c["start"], c["end"], c["text"]
            if e <= a:
                r = keep(s, e, t)
                if r: out.append(r)
            elif s >= b:
                r = keep(s - shift, e - shift, t)
                if r: out.append(r)
            elif s >= a and e <= b:
                ns = a + (s - a) / f
                ne = a + (e - a) / f
                r = keep(ns, ne, t)
                if r: out.append(r)
            elif s < a and e > b:
                r1 = keep(s, a, t)
                if r1: out.append(r1)
                r2 = keep(b + shift - d, e - shift, t)
                if r2: out.append(r2)
            elif s < a and e <= b:
                r1 = keep(s, a, t)
                if r1: out.append(r1)
                ns = a
                ne = a + (e - a) / f
                r2 = keep(ns, ne, t)
                if r2: out.append(r2)
            elif s >= a and e > b:
                ns = a + (s - a) / f
                ne = a + (b - a) / f
                r1 = keep(ns, ne, t)
                if r1: out.append(r1)
                r2 = keep(b + shift - d, e - shift, t)
                if r2: out.append(r2)
        return out

    return out


# --------------------------------------------------------------------------- #
# timeline helpers — pure functions over segments
# --------------------------------------------------------------------------- #
def _atempo(speed: float) -> str:
    """atempo handles 0.5..2.0 per instance; chain for anything outside."""
    chain, left = [], float(speed)
    while left > 2.0:
        chain.append("atempo=2.0")
        left /= 2.0
    while left < 0.5:
        chain.append("atempo=0.5")
        left /= 0.5
    chain.append(f"atempo={round(left, 4)}")
    return ",".join(chain)


def _tlen(s: dict) -> float:
    return max(0.0, (s["src_out"] - s["src_in"]) / s["speed"])


def _offsets(segs):
    out, t = [], 0.0
    for s in segs:
        d = _tlen(s)
        out.append((s, t, t + d))
        t += d
    return out


def _slice(s: dict, seg_t0: float, lo: float, hi: float) -> dict:
    return {**s,
            "src_in": s["src_in"] + (lo - seg_t0) * s["speed"],
            "src_out": s["src_in"] + (hi - seg_t0) * s["speed"],
            "speed": s["speed"], "muted": s["muted"]}


def _split(segs, ranges):
    """-> [(segment, in_range)]. Each range is in CURRENT timeline seconds."""
    res = []
    for s, ts, te in _offsets(segs):
        cuts = []
        for a, b in ranges:
            lo, hi = max(a, ts), min(b, te)
            if hi - lo > 1e-3:
                cuts.append((lo, hi))
        cuts.sort()
        cur = ts
        for lo, hi in cuts:
            if lo - cur > 1e-3:
                res.append((_slice(s, ts, cur, lo), False))
            res.append((_slice(s, ts, lo, hi), True))
            cur = hi
        if te - cur > 1e-3:
            res.append((_slice(s, ts, cur, te), False))
    return res


def _keep(segs, ranges, inside: bool):
    return [p for p, hit in _split(segs, ranges) if hit is inside]


def _retime(segs, rng, factor: float):
    out = []
    for p, hit in _split(segs, [rng]):
        if hit and p.get("kind") != "card":
            out.append({**p, "speed": round(p["speed"] * factor, 3)})
        else:
            out.append(p)
    return out


def _insert_card(segs, at: float, card: dict):
    """Place a card segment at timeline second `at`, splitting the src segment
    it lands inside. Clamped to the timeline so an op can never point past the end."""
    total = sum(_tlen(s) for s in segs)
    at = max(0.0, min(float(at), total))
    out, t = [], 0.0
    placed = False
    for s in segs:
        d = _tlen(s)
        if placed or at > t + d - 1e-6:
            out.append(s)
        elif at < t + 1e-6:
            out.extend([card, s])
            placed = True
        else:
            out.extend([_slice(s, t, t, at), card, _slice(s, t, at, t + d)])
            placed = True
        t += d
    if not placed:
        out.append(card)
    return out


def _mute(segs, rng):
    return [{**p, "muted": True} if hit else p for p, hit in _split(segs, [rng])]


# --------------------------------------------------------------------------- #
# the edit list
# --------------------------------------------------------------------------- #
class Project:
    """source + ops. State is replay(ops); nothing else is stored."""

    def __init__(self, source: str, name: str | None = None):
        self.source = os.path.abspath(source)
        if not os.path.exists(self.source):
            raise ValueError(f"source not found: {self.source}")
        self.name = name or os.path.splitext(os.path.basename(self.source))[0]
        self.ops: list[dict] = []
        self.meta: dict = {}

    # ---------- persistence ----------
    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"name": self.name, "source": self.source, "ops": self.ops,
                       "meta": self.meta}, f, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path) as f:
            d = json.load(f)
        p = cls(d["source"], d.get("name"))
        p.ops = d.get("ops", [])
        p.meta = d.get("meta", {})
        return p

    # ---------- ops (all pure data) ----------
    def op(self, op_name: str, **kw) -> dict:
        # param is not called `kind` because card ops carry their own `kind`
        entry = {"op": op_name, **kw}
        self.ops.append(entry)
        return entry

    def _card_spans(self) -> list[tuple]:
        segs, _ = self.timeline()
        return [(s["start"], s["start"] + s["duration"])
                for s in segs if s.get("kind") == "card"]

    def _expand_over_cards(self, ranges):
        """Cards are atomic: a snip that grazes one removes the whole card.

        Without this a frame-precision cut landing 0.1s inside a title card would
        leave a 1.1s sliver of card — worse than either keeping or dropping it.
        """
        cards = self._card_spans()
        out = []
        for a, b in ranges:
            for cs, ce in cards:
                if a < ce - 1e-6 and b > cs + 1e-6:      # any overlap at all
                    a, b = min(a, cs), max(b, ce)
            out.append((a, b))
        return out

    def snip(self, start: float, end: float) -> dict:
        """Remove [start, end) as measured on the CURRENT timeline, in seconds."""
        if end <= start:
            raise ValueError("snip: end must be after start")
        (a, b), = self._expand_over_cards([(start, end)])
        return self.op("snip", start=round(a, 3), end=round(b, 3))

    def snip_many(self, ranges) -> dict:
        return self.op("snip", ranges=[[round(a, 3), round(b, 3)]
                                       for a, b in self._expand_over_cards(ranges)])

    def card(self, png: str, seconds: float = 1.2, at: float | None = None,
             kind: str = "title") -> dict:
        """Insert a rendered card as a timeline segment at `at` (None = end)."""
        if not os.path.exists(png):
            raise ValueError(f"card image not found: {png}")
        if not 0.2 <= float(seconds) <= 10.0:
            raise ValueError("card seconds must be within 0.2..10.0")
        return self.op("card", png=os.path.abspath(png), seconds=round(float(seconds), 3),
                       at=None if at is None else round(float(at), 3), kind=kind)

    def trim(self, start: float | None = None, end: float | None = None) -> dict:
        return self.op("trim", start=start, end=end)

    def speed(self, factor: float, start: float | None = None,
              end: float | None = None) -> dict:
        if not 0.1 <= factor <= 4.0:
            raise ValueError("speed: factor must be within 0.1..4.0")
        return self.op("speed", factor=round(factor, 3), start=start, end=end)

    def caption(self, text: str, start: float, end: float) -> dict:
        if len(text.split()) > 7:
            raise ValueError("caption: seven words maximum (brand rule)")
        return self.op("caption", text=text, start=round(start, 3), end=round(end, 3))

    def mute(self, start: float, end: float) -> dict:
        return self.op("mute", start=round(start, 3), end=round(end, 3))

    def undo(self) -> dict | None:
        return self.ops.pop() if self.ops else None

    def drop(self, index: int) -> dict:
        """Remove ONE op anywhere in the history (not just the last).

        This is what makes revision real: an edit you regret from ten ops ago is
        gone without throwing away the nine after it.
        """
        i = int(index)
        if not 0 <= i < len(self.ops):
            raise ValueError(f"no op at index {i} (have {len(self.ops)})")
        return self.ops.pop(i)

    _EDITABLE = {"snip": {"start", "end", "ranges"},
                 "trim": {"start", "end"},
                 "speed": {"start", "end", "factor"},
                 "mute": {"start", "end"},
                 "caption": {"text", "start", "end"},
                 "card": {"seconds", "at", "kind", "png"}}

    def edit_op(self, index: int, **changes) -> dict:
        i = int(index)
        if not 0 <= i < len(self.ops):
            raise ValueError(f"no op at index {i} (have {len(self.ops)})")
        entry = self.ops[i]
        allowed = self._EDITABLE.get(entry["op"], set())
        bad = set(changes) - allowed
        if bad:
            raise ValueError(f"cannot edit {sorted(bad)} on a {entry['op']} op")
        if "text" in changes and len(str(changes["text"]).split()) > 7:
            raise ValueError("caption: seven words maximum (brand rule)")
        self.ops[i] = {**entry, **changes}
        return self.ops[i]

    def relink(self, new_source: str) -> str:
        """Point the project at a new file, keeping every edit.

        Re-shooting footage should not cost you the cut: same ops, new take.
        """
        src = os.path.abspath(os.path.expanduser(new_source))
        if not os.path.exists(src):
            raise ValueError(f"source not found: {src}")
        info = probe(src)
        old = probe(self.source)["duration"]
        if abs(info["duration"] - old) > 0.5:
            raise ValueError(
                f"new source is {info['duration']:.1f}s, project was cut for "
                f"{old:.1f}s — edits would land on the wrong frames")
        self.source = src
        return src

    def reset(self) -> None:
        self.ops = []

    # ---------- replay ----------
    def timeline(self) -> tuple[list[dict], list[dict]]:
        """Pure replay: source + ops -> segments + captions. No ffmpeg involved,
        so undo, preview and re-cutting are free."""
        total = probe(self.source)["duration"]
        segs = [{"src_in": 0.0, "src_out": total, "speed": 1.0, "muted": False}]
        caps: list[dict] = []

        for idx, entry in enumerate(self.ops):
            kind = entry["op"]
            if kind == "caption":
                caps.append({"start": entry["start"], "end": entry["end"],
                             "text": entry["text"], "op_index": idx})
            elif kind == "snip":
                ranges = [tuple(r) for r in
                          (entry.get("ranges") or [(entry["start"], entry["end"])])]
                segs = _keep(segs, ranges, inside=False)
                # Captions move with the material. Applied high-to-low so each
                # range's coordinates are still valid when it is transformed.
                for a, b in sorted(ranges, reverse=True):
                    caps = transform_captions(caps, "snip", (a, b))
            elif kind == "trim":
                lo = 0.0 if entry.get("start") is None else float(entry["start"])
                hi = float("inf") if entry.get("end") is None else float(entry["end"])
                segs = _keep(segs, [(lo, hi)], inside=True)
                caps = transform_captions(caps, "trim", (lo, min(hi, total)))
            elif kind == "speed":
                lo = 0.0 if entry.get("start") is None else float(entry["start"])
                hi = float("inf") if entry.get("end") is None else float(entry["end"])
                segs = _retime(segs, (lo, hi), float(entry["factor"]))
                caps = transform_captions(caps, "speed", (lo, min(hi, total)),
                                          float(entry["factor"]))
            elif kind == "mute":
                segs = _mute(segs, (entry["start"], entry["end"]))
            elif kind == "card":
                d = float(entry["seconds"])
                card = {"kind": "card", "png": entry["png"],
                        "src_in": 0.0, "src_out": d, "speed": 1.0, "muted": True}
                at = sum(_tlen(s) for s in segs) if entry.get("at") is None \
                    else float(entry["at"])
                segs = _insert_card(segs, at, card)
            else:
                raise ValueError(f"unknown op {kind!r}")

        timeline = []
        t = 0.0
        for s in segs:
            d = _tlen(s)
            row = {"src_in": round(s["src_in"], 3),
                   "src_out": round(s["src_out"], 3),
                   "speed": s["speed"], "muted": s["muted"],
                   "start": round(t, 3), "duration": round(d, 3),
                   "kind": s.get("kind", "src")}
            if s.get("kind") == "card":
                row["png"] = s.get("png")
            timeline.append(row)
            t += d
        return timeline, sorted(caps, key=lambda c: c["start"])


    # ---------- render ----------
    def render(self, out: str, captions: bool = True, loud: bool = True,
               size=(W, H), fps: int = FPS) -> dict:
        segs, caps = self.timeline()
        if not segs:
            raise ValueError("nothing left to render")
        info = probe(self.source)
        tmp = tempfile.mkdtemp(prefix="video-studio-")
        try:
            graph, labels = [], []
            # one input per distinct card image, so five cards cost one PNG decode
            cards = {}
            for s in segs:
                if s.get("kind") == "card":
                    cards.setdefault(s["png"], len(cards) + 1)
            extra_inputs = []
            for png in cards:
                extra_inputs += ["-loop", "1", "-i", png]

            for i, s in enumerate(segs):
                dur = s["duration"]
                if s.get("kind") == "card":
                    k = cards[s["png"]]
                    graph.append(
                        f"[{k}:v]scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,"
                        f"pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2:color=0x0b0d10,"
                        f"fps={fps},trim=start={s['src_in']}:end={s['src_out']},"
                        f"setpts=PTS-STARTPTS,setsar=1[v{i}]")
                    graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},"
                                 f"asetpts=PTS-STARTPTS[a{i}]")
                    labels.append(f"[v{i}][a{i}]")
                    continue
                v = (f"[0:v]trim=start={s['src_in']}:end={s['src_out']},"
                     f"setpts=(PTS-STARTPTS)/{s['speed']},"
                     f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease,"
                     f"pad={size[0]}:{size[1]}:(ow-iw)/2:(oh-ih)/2:color=0x0b0d10,"
                     f"fps={fps},setsar=1[v{i}]")
                graph.append(v)
                if info["has_audio"]:
                    graph.append(
                        f"[0:a]atrim=start={s['src_in']}:end={s['src_out']},"
                        f"asetpts=PTS-STARTPTS,{_atempo(s['speed'])},"
                        f"volume={'0' if s.get('muted') else '1'},"
                        f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]")
                labels.append(f"[v{i}][a{i}]" if info["has_audio"] else f"[v{i}]")

            if info["has_audio"]:
                graph.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=1[vc][ac]")
                maps = ["-map", "[vc]", "-map", "[ac]"]
                n = len(segs)
            else:
                graph.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=0[vc]")
                graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:"
                             f"{sum(s['duration'] for s in segs)}[ac]")
                maps = ["-map", "[vc]", "-map", "[ac]"]
                n = len(segs)

            rough = os.path.join(tmp, "rough.mp4")
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", self.source, *extra_inputs,
                   "-filter_complex", ";".join(graph), *maps,
                   "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p", "-r", str(fps),
                   "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", rough]
            r = _run(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"render failed: {r.stderr[-700:]}")

            stage = rough
            if loud:
                stage = os.path.join(tmp, "loud.mp4")
                self._normalise(rough, stage)

            if captions and caps:
                ass = os.path.join(tmp, "caps.ass")
                _write_ass(caps, ass)
                final = out
                r = _run(["ffmpeg", "-y", "-v", "error", "-i", stage,
                          "-vf", f"ass={ass}", "-c:v", "libx264", "-crf", "18",
                          "-preset", "medium", "-pix_fmt", "yuv420p",
                          "-c:a", "copy", final])
                if r.returncode != 0:
                    raise RuntimeError(f"caption pass failed: {r.stderr[-500:]}")
            else:
                shutil.copy(stage, out)
            return self.verify(out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _normalise(src: str, dst: str) -> None:
        r = _run(["ffmpeg", "-hide_banner", "-i", src, "-af",
                  "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                  "-f", "null", "-"])
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
        if not m:
            shutil.copy(src, dst)
            return
        d = json.loads(m.group(0))
        af = (f"loudnorm=I={LOUDNESS}:TP=-1.5:LRA=11:linear=true:"
              f"measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
              f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:"
              f"offset={d['target_offset']}")
        _run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af,
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", dst])

    # ---------- timeline-space audio ----------
    def timeline_audio(self, out: str) -> str:
        """Render only the audio of the CURRENT timeline.

        `tighten` must measure the timeline, not the source: after a snip or a
        speed op, source seconds and timeline seconds are different timelines and
        a source-time cut lands on the wrong material (found in review by the
        GLM lane; the bug was ours, the lead was theirs).
        """
        segs, _ = self.timeline()
        info = probe(self.source)
        if not info["has_audio"]:
            raise ValueError("source has no audio track")
        graph, labels = [], []
        for i, s in enumerate(segs):
            graph.append(
                f"[0:a]atrim=start={s['src_in']}:end={s['src_out']},"
                f"asetpts=PTS-STARTPTS,{_atempo(s['speed'])},"
                f"volume={'0' if s.get('muted') else '1'}[a{i}]")
            labels.append(f"[a{i}]")
        graph.append("".join(labels) + f"concat=n={len(segs)}:v=0:a=1[out]")
        _run(["ffmpeg", "-y", "-v", "error", "-i", self.source,
              "-filter_complex", ";".join(graph), "-map", "[out]",
              "-ar", "48000", "-ac", "1", out])
        return out

    def analyze_timeline(self, noise_db: float = -35.0, min_silence: float = 0.6,
                         pad: float = 0.12) -> dict:
        """Pause analysis in TIMELINE seconds, with head/tail protected."""
        import tempfile
        segs, _ = self.timeline()
        total = sum(s["duration"] for s in segs)
        tmp = tempfile.mkdtemp(prefix="video-studio-audio-")
        try:
            wav = self.timeline_audio(os.path.join(tmp, "timeline.wav"))
            spans = _silences(wav, noise_db, min(min_silence, 0.45))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        cuts, head, tail = [], 0.0, 0.0
        for a, b in spans:
            if b - a < min_silence:
                continue
            if a <= 0.05:
                head = round(b - a, 2)
                continue
            if b >= total - 0.05:
                tail = round(b - a, 2)
                continue
            if (b - pad) - (a + pad) > 0.05:
                cuts.append((round(a + pad, 2), round(b - pad, 2)))
        return {"duration": round(total, 2), "silences": spans,
                "suggested_tighten": cuts, "head_silence_seconds": head,
                "tail_silence_seconds": tail,
                "would_remove_seconds": round(sum(b - a for a, b in cuts), 2),
                "space": "timeline"}

    # ---------- verify ----------
    def verify(self, path: str, max_seconds: float = 90.0) -> dict:
        info = probe(path)
        checks = []

        def add(name, ok, detail):
            checks.append({"check": name, "pass": bool(ok), "detail": detail})

        add("duration <= cap", info["duration"] <= max_seconds + 0.5,
            f"{info['duration']:.2f}s")
        add("1080p", (info["width"], info["height"]) == (W, H),
            f"{info['width']}x{info['height']}")
        add("30 fps", abs(info["fps"] - FPS) < 0.01, f"{info['fps']:g}")
        add("has audio", info["has_audio"], info["audio_codec"] or "none")

        if info["has_audio"]:
            ln = _loudness(path)
            if ln:
                add("loudness -18..-14 LUFS", -18 <= ln["integrated_lufs"] <= -14,
                    f"{ln['integrated_lufs']:.1f} LUFS")
                add("no clipping", ln["true_peak_db"] <= -0.8,
                    f"TP {ln['true_peak_db']:.1f} dB")
            spans = _silences(path)
            # Only long internal gaps count. Title/end cards are silent on purpose
            # and sub-1.5s pauses are natural speech breathing.
            dead = [(a, b) for a, b in spans
                    if (b - a) >= 1.5 and a > 3.0 and b < info["duration"] - 3.0]
            add("no dead air >1.5s", not dead, f"{len(dead)} gap(s) {dead[:3]}")

        r = _run(["ffmpeg", "-hide_banner", "-i", path, "-vf",
                  "blackdetect=d=0.4:pix_th=0.03", "-an", "-f", "null", "-"])
        add("no black gaps >0.4s", "black_start" not in r.stderr,
            f"{r.stderr.count('black_start')} span(s)")

        r = _run(["ffmpeg", "-hide_banner", "-i", path, "-t", "5", "-vf",
                  "select='gt(scene,0.015)',metadata=print", "-an", "-f", "null", "-"])
        add("motion in first 5s", r.stderr.count("scene_score") >= 1,
            f"{r.stderr.count('scene_score')} change(s)")

        return {
            "file": os.path.abspath(path),
            "duration": info["duration"],
            "pass": all(c["pass"] for c in checks),
            "checks": checks,
            "failures": [c["check"] for c in checks if not c["pass"]],
        }


# --------------------------------------------------------------------------- #
# captions
# --------------------------------------------------------------------------- #
ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,JetBrains Mono,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,4,18,0,1,110,110,96,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _write_ass(caps, path: str) -> str:
    lines = [ASS_HEAD]
    for c in caps:
        text = c["text"].replace("\n", " ")
        lines.append(f"Dialogue: 0,{_ts(c['start'])},{_ts(c['end'])},Cap,,0,0,0,,{text}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------- #
# analysis — the part the model is actually good for
# --------------------------------------------------------------------------- #
def analyze(path: str, noise_db: float = -35.0, min_silence: float = 0.6,
            pad: float = 0.12) -> dict:
    """Measured facts + a proposed cut list. The agent proposes, you approve."""
    info = probe(path)
    spans = _silences(path, noise_db, min(min_silence, 0.45))
    scenes = _scenes(path)
    lufs = _loudness(path)

    # Head and tail silence belong to the title / end cards. Cutting them would
    # silently delete the cards themselves, so they are reported, never proposed.
    tight, head, tail = [], 0.0, 0.0
    for a, b in spans:
        if b - a < min_silence:
            continue
        if a <= 0.05:
            head = round(b - a, 2)
            continue
        if b >= info["duration"] - 0.05:
            tail = round(b - a, 2)
            continue
        if (b - pad) - (a + pad) > 0.05:
            tight.append((round(a + pad, 2), round(b - pad, 2)))
    removed = sum(b - a for a, b in tight)

    return {
        "file": info["path"],
        "duration": info["duration"],
        "resolution": f"{info['width']}x{info['height']}",
        "fps": info["fps"],
        "silences": spans,
        "scene_changes": scenes,
        "loudness": lufs,
        "suggested_tighten": tight,
        "head_silence_seconds": head,      # kept: title card
        "tail_silence_seconds": tail,      # kept: end card
        "would_remove_seconds": round(removed, 2),
        "would_be_seconds": round(info["duration"] - removed, 2),
        "first_motion_at": scenes[0] if scenes else None,
        "hook_is_static": (not scenes) or scenes[0] > 5.0,
    }
