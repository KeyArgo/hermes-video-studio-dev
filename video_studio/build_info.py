"""Build stamp: which code is actually running, answerable from inside the product.

Two numbers, never one (VERSIONING-STANDARD §1):
  * the release target lives in `plugin.yaml` (`1.0.0`) — what the next release will be called;
  * this build is `1.0.0-dev.N` where N is `git rev-list --count HEAD`, generated into
    `BUILD_INFO.json` at deploy time and never committed (a committed stamp is stale by the
    next commit).

The stamp is written by `scripts/build_info.py` and read by `GET /version`; the app shows it as
a chip in the header. If the file is missing, the reader says so rather than inventing a number.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_FILE = ROOT / "BUILD_INFO.json"


def _git(*args: str, default: str = "") -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or default
    except Exception:  # noqa: BLE001
        return default


def release_target() -> str:
    """`version:` from plugin.yaml — the release, never a build number."""
    text = (ROOT / "plugin.yaml").read_text()
    for line in text.splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def generate(channel: str = "dev") -> dict:
    release = release_target()
    count = _git("rev-list", "--count", "HEAD", default="0")
    commit = _git("rev-parse", "HEAD")
    commit_date = _git("show", "-s", "--format=%cI", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    payload = {
        "version": f"{release}-{channel}.{count}" if channel else f"{release}+{count}",
        "release": release,
        "commit": commit,
        "commit_date": commit_date,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "dirty": dirty,
        "channel": channel,
    }
    BUILD_FILE.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def read() -> dict:
    """What is on disk. A missing or unreadable stamp is reported as such, never guessed."""
    if not BUILD_FILE.is_file():
        return {"version": None, "release": release_target(), "commit": None,
                "dirty": None, "channel": None,
                "note": "BUILD_INFO.json is missing — run scripts/build_info.py"}
    try:
        return json.loads(BUILD_FILE.read_text())
    except json.JSONDecodeError:
        return {"version": None, "release": release_target(), "commit": None,
                "dirty": None, "channel": None, "note": "BUILD_INFO.json is not valid JSON"}


def chip() -> str:
    """The one line a user can read out in a bug report."""
    info = read()
    if not info.get("version"):
        return info.get("note") or "unversioned build"
    short = (info.get("commit") or "")[:8]
    date = (info.get("commit_date") or info.get("built_at") or "")[:10]
    line = f"v{info['version']} · {date} · {short}".rstrip(" ·")
    return line + (" · modified" if info.get("dirty") else "")


def main() -> None:
    print(json.dumps(generate(), indent=2))


if __name__ == "__main__":
    main()
