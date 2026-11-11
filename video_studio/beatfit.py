"""Cut to the music: a beat grid, and shot lengths that land on it.

A cut feels intentional when its cuts land on the music. That is the difference between a slide
show with a soundtrack and a film. This module knows three things:

    grid()        the beat times of the track under this film, from the track's own tempo
    offset()      where the first beat falls, measured from the audio (numpy only, no new deps)
    fit()         the shot lengths that put every cut on a beat, keeping the film's total length

The tempo is **not** guessed by us when the Music Studio take already carries `bpm`: that number
came from the engine that made the song and is authoritative. Detection is used only for the
*phase* (where beat one lands), which is the thing metadata cannot tell you.

Two honest constraints:
- the film's total length is set by the music bed, so only interior cuts move;
- a beat never becomes shorter than MIN_BEAT, because snapping to a grid must not produce a
  one-frame flash to make a number look good.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MIN_BEAT = 1.2          # seconds; below this a shot reads as a flash, not a shot
EPS = 30e-3             # 30 ms ≈ one frame at 30 fps: "on the beat" means inside this


def beat_length(bpm: float | None) -> float:
    if not bpm or bpm <= 0:
        raise ValueError("no tempo for this film — attach a track that carries a bpm")
    return 60.0 / float(bpm)


def grid(total: float, bpm: float, offset: float = 0.0) -> list[float]:
    """Beat times across `total` seconds, starting at `offset` (offset is folded into the bar)."""
    step = beat_length(bpm)
    off = offset % step
    times, t = [], off
    while t <= total + 1e-9:
        if t > 1e-9:
            times.append(round(t, 4))
        t += step
    return times


def _onsets(path: Path, start: float, seconds: float, sr: int = 22050) -> list[float]:
    """Spectral-flux onsets in the first `seconds` of a track from `start` (frame times, seconds)."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
                          "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.size < sr // 4:
        return []
    hop, win = 512, 1024
    n = 1 + (x.size - win) // hop
    frames = np.lib.stride_tricks.as_strided(x, shape=(n, win),
                                             strides=(x.strides[0] * hop, x.strides[0]))
    spec = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1))
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    env = flux - flux.mean()
    env = np.maximum(env, 0)
    if not env.size:
        return []
    thr = env.mean() + 0.8 * env.std()
    times = (np.arange(env.size) + 1) * hop / sr
    return [float(times[i]) for i in range(1, env.size - 1)
            if env[i] > thr and env[i] >= env[i - 1] and env[i] > env[i + 1]]


def offset(track_path: Path | None, start: float, bpm: float) -> dict:
    """Where beat one falls, relative to the point of the track the film starts at.

    Returns `{offset, source, confidence}`. `source` is `'audio'` when onsets were measured and
    `'none'` when the file was unavailable — in that case the grid starts at 0 and the caller is
    told, rather than being handed a silently invented phase.
    """
    step = beat_length(bpm)
    if not track_path or not Path(track_path).is_file():
        return {"offset": 0.0, "source": "none", "confidence": 0.0}
    try:
        hits = _onsets(Path(track_path), start, min(8.0, step * 32))
    except Exception as exc:                        # never let analysis break a cut
        return {"offset": 0.0, "source": f"error: {exc}", "confidence": 0.0}
    if not hits:
        return {"offset": 0.0, "source": "none", "confidence": 0.0}
    # fold every onset into one beat period and take the circular median: a single early
    # transient cannot drag the whole grid the way a plain mean would
    folded = sorted(h % step for h in hits)
    mid = folded[len(folded) // 2]
    return {"offset": round(mid, 4), "source": "audio",
            "confidence": round(min(1.0, len(hits) / 12.0), 2)}


def on_beat(boundaries: list[float], bpm: float, offset_s: float = 0.0,
            tolerance: float = EPS) -> dict:
    """How many interior cuts sit on a beat, with the distance each one is off by."""
    step = beat_length(bpm)
    rows = []
    for b in boundaries:
        k = round((b - offset_s) / step)
        target = offset_s + k * step
        rows.append({"at": round(b, 3), "nearest_beat": round(target, 3),
                     "off": round(b - target, 3), "on": abs(b - target) <= tolerance})
    interior = rows[:-1] if rows else []
    hit = sum(1 for r in interior if r["on"])
    return {"cuts": rows, "on": hit, "of": len(interior),
            "percent": round(100.0 * hit / len(interior), 1) if interior else 0.0,
            "tolerance": tolerance}


def fit(durations: list[float], bpm: float, offset_s: float = 0.0,
        tolerance: float = EPS) -> dict:
    """Snap interior cut points to the beat grid; the film keeps its total length.

    Only boundaries move, and each one moves to its nearest beat. That keeps the change local:
    a cut shifting by 0.2 s takes 0.2 s from one neighbour and gives it to the other, so the
    music under the film never has to be re-cut.
    """
    if len(durations) < 2:
        return {"ok": False, "reason": "a film with one shot has no interior cuts to fit"}
    step = beat_length(bpm)
    total = sum(durations)
    bounds, acc = [], 0.0
    for d in durations:
        acc += d
        bounds.append(round(acc, 4))

    moved, new_durations, prev = [], [], 0.0
    for i, b in enumerate(bounds):
        if i == len(bounds) - 1:
            target = round(total, 4)                 # the end is fixed by the music bed
        else:
            target = offset_s + round((b - offset_s) / step) * step
            # an exact half-beat is a tie, and a tie is exactly the cut we want to move:
            # the epsilon keeps floating-point noise from reverting it (11.0 s at 90 bpm)
            if abs(target - b) > step / 2 + 1e-6:
                target = b
        target = round(max(target, prev + MIN_BEAT), 4)   # never compress a shot into a flash
        if target >= total - MIN_BEAT * (len(bounds) - i - 1):
            target = round(b, 4)                     # no room left: leave this cut where it is
        new_durations.append(round(target - prev, 4))
        if abs(target - b) > 1e-6:
            moved.append({"cut": i + 1, "was": round(b, 3), "now": round(target, 3),
                          "delta": round(target - b, 3)})
        prev = target

    if abs(sum(new_durations) - total) > 0.05:
        return {"ok": False, "reason": f"snapping would change the film's length "
                                       f"({sum(new_durations):.2f}s vs {total:.2f}s) — the bed would need cutting"}
    return {"ok": True, "durations": new_durations, "moved": moved,
            "total": round(sum(new_durations), 3), "bpm": bpm, "beat": round(step, 4)}
