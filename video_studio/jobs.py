"""Every render this studio starts, as a process the person can see and stop.

The failure this exists to prevent: a render keeps running after the window that started it is
gone, taking several cores, with nothing on screen saying so and no way to stop it.

Three rules:

1. **A child is tracked before it is useful.** Every renderer (melt, hyperframes/node, ffmpeg) is
   spawned through `run()` with its own process group, and its pid is written to a registry file
   *before* the command starts producing anything.
2. **A dead parent is not a dead child.** The host runs detached (it must survive the pane), so
   its children would otherwise survive the host too. `reap_all()` is called on shutdown and kills
   each group deliberately.
3. **Visible, and lower priority than the person's own work.** Renders run under `nice` (and an
   optional CPU affinity) so an export cannot make the desktop feel broken, and `/api/jobs` answers
   what is running right now — including a CPU% per process, measured from /proc.

The registry is a JSON file so it survives a host restart: a stale entry whose pid is gone is
reported as `orphaned` rather than silently forgotten.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

REGISTRY = Path(os.environ.get("HEWN_VIDEO_STUDIO_DATA") or
                Path.home() / ".hermes/plugin-data/hewn-video-studio") / "render-jobs.json"

NICE = int(os.environ.get("VIDEO_STUDIO_RENDER_NICE", "10"))     # 0 = no change, 19 = slowest
CPUS = os.environ.get("VIDEO_STUDIO_RENDER_CPUS", "").strip()      # e.g. "0-3" — empty = all


def _read() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(REGISTRY)


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def _cpu_seconds(pid: int) -> float | None:
    """utime+stime in seconds, straight from /proc — no new dependency for a real number."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            parts = fh.read().split()
        return (int(parts[13]) + int(parts[14])) / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()[:200]
    except Exception:
        return ""


def argv(cmd: list[str]) -> list[str]:
    """Apply the politeness policy: lower priority, and optionally a core set."""
    out = list(cmd)
    if CPUS and shutil.which("taskset"):
        out = ["taskset", "-c", CPUS] + out
    if NICE > 0 and shutil.which("nice"):
        out = ["nice", "-n", str(NICE)] + out
    return out


def start(kind: str, cmd: list[str], film: str | None = None, label: str | None = None,
          cwd: str | None = None, job_id: str | None = None) -> dict:
    """Spawn a tracked renderer. Returns the job record (id, pid, cmd)."""
    job_id = job_id or f"{kind}-{int(time.time() * 1000) % 10**9}"
    full = argv(cmd)
    proc = subprocess.Popen(full, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)   # own group => cancellable as one
    data = _read()
    data[job_id] = {"id": job_id, "kind": kind, "film": film, "label": label or kind,
                    "pid": proc.pid, "pgid": proc.pid, "started": time.time(),
                    "cmd": " ".join(full)[:400], "state": "running", "nice": NICE, "cpus": CPUS}
    _write(data)
    return {"id": job_id, "pid": proc.pid, "cmd": full, "proc": proc}


def finish(job_id: str, state: str = "done", detail: str | None = None) -> None:
    data = _read()
    if job_id in data:
        data[job_id].update({"state": state, "ended": time.time()})
        if detail:
            data[job_id]["detail"] = detail[:400]
        _write(data)


def list_active() -> list[dict]:
    """What is running now, with a measured CPU figure — never a guess."""
    data = _read()
    rows = []
    for job in data.values():
        pid = job.get("pid")
        alive = _alive(pid)
        cpu = _cpu_seconds(pid) if alive and pid else None
        prev = job.get("cpu_seen")
        if cpu is not None and prev:
            job["cpu_pct"] = round((cpu - prev) / max(1e-6, time.time() - job.get("cpu_at", time.time())) * 100, 1)
        if cpu is not None:
            job["cpu_seen"], job["cpu_at"] = cpu, time.time()
        rows.append({
            "id": job["id"], "kind": job.get("kind"), "film": job.get("film"),
            "label": job.get("label"), "pid": pid, "alive": alive,
            "state": job.get("state") if alive or job.get("state") == "done" else "orphaned",
            "elapsed": round(time.time() - job.get("started", time.time()), 1),
            "cpu_pct": job.get("cpu_pct"), "cmd": _cmdline(pid) if alive and pid else job.get("cmd"),
            "nice": job.get("nice"), "cpus": job.get("cpus"),
        })
    _write(data)          # keep the measured cpu figures
    return sorted(rows, key=lambda r: r["started"] if "started" in r else r["elapsed"])


def cancel(job_id: str, wait: float = 6.0) -> dict:
    """Stop one job: SIGTERM to its process group, then SIGKILL if it does not go."""
    data = _read()
    job = data.get(job_id)
    if not job:
        return {"ok": False, "reason": f"no job {job_id}"}
    pid = job.get("pid")
    if not _alive(pid):
        finish(job_id, "cancelled", "process was already gone")
        return {"ok": True, "already": True}
    killed = _kill_group(pid, wait)
    finish(job_id, "cancelled" if killed else "cancel-failed",
           "stopped" if killed else "process ignored SIGTERM and SIGKILL")
    return {"ok": killed, "pid": pid, "id": job_id}


def _kill_group(pid: int, wait: float = 6.0) -> bool:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return True
    deadline = time.time() + wait
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.15)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.4)
    return not _alive(pid)


def reap_all(wait: float = 5.0) -> dict:
    """Kill every tracked child. Called on host shutdown, so 'closed the window' means stopped."""
    data = _read()
    stopped, already = [], []
    for job_id, job in list(data.items()):
        pid = job.get("pid")
        if _alive(pid):
            stopped.append(job_id) if _kill_group(pid, wait) else None
            finish(job_id, "reaped", "killed at host shutdown")
        else:
            already.append(job_id)
    return {"stopped": stopped, "already_gone": already}


def gc(keep: int = 20, max_age_hours: float = 24.0) -> dict:
    """Tidy the registry: drop finished entries that are old or surplus, never a live one.

    A registry that only grows is its own leak — after a few days the file is a history of every
    render ever run, and `list_active` walks all of it on every poll.
    """
    data = _read()
    now = time.time()
    live, dead = {}, []
    for job_id, job in data.items():
        if _alive(job.get("pid")):
            live[job_id] = job
        else:
            dead.append(job)
    dead.sort(key=lambda j: j.get("started", 0), reverse=True)
    kept = [j for j in dead if now - j.get("started", 0) < max_age_hours * 3600][:keep]
    for job in kept:
        live[job["id"]] = job
    removed = len(data) - len(live)
    _write(live)
    return {"kept": len(live), "removed": removed, "live": sum(1 for j in live.values() if _alive(j.get("pid")))}


def startup_cleanup() -> dict:
    """Called when the host starts: nothing from a previous run may still be burning a core."""
    reaped = reap_all(wait=2.5)
    tidied = gc()
    return {"reaped": reaped, "registry": tidied}


def idle_report() -> dict:
    """How much this plugin costs when it is doing nothing — measured, not asserted."""
    host_pid = os.getpid()
    cpu = _cpu_seconds(host_pid)
    rows = list_active()
    running = [r for r in rows if r["alive"]]
    return {
        "host_pid": host_pid,
        "host_cpu_seconds": cpu,
        "active_jobs": len(running),
        "jobs": running,
        "policy": {"nice": NICE, "cpus": CPUS or "all cores"},
        "note": ("The host is idle when no job is listed: it serves HTTP and holds no timer that "
                 "renders. Renders run under nice(10) so they yield to whatever you are doing."),
    }
