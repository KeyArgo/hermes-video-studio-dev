#!/usr/bin/env bash
# Deploy this plugin into the Hermes plugins directory and stamp the build.
#
# The installed copy does not follow the source, and Hermes binds tools at session start, so a
# deploy is: copy files, regenerate BUILD_INFO.json *in the installed copy*, restart the studio
# host. Doing it by hand is how you end up debugging the previous version (it happened twice).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERMES_PLUGIN_DIR:-$HOME/.hermes/plugins/video-studio}"

echo "→ $SRC ⇒ $DEST"
mkdir -p "$DEST/video_studio" "$DEST/studio" "$DEST/tests"

cp "$SRC"/video_studio/*.py  "$DEST/video_studio/"
cp "$SRC"/video_studio/*.js  "$DEST/video_studio/" 2>/dev/null || true
cp "$SRC"/studio/*           "$DEST/studio/"
cp "$SRC"/tests/*.py         "$DEST/tests/"
cp "$SRC"/plugin.yaml "$SRC"/dashboard/plugin_api.py "$DEST/" 2>/dev/null || true
cp "$SRC"/dashboard/plugin_api.py "$DEST/dashboard/" 2>/dev/null || true
cp "$SRC"/BUILD_INFO.json    "$DEST/BUILD_INFO.json" 2>/dev/null || true

# The stamp must describe the installed tree. If that tree has no git history (a plain copy),
# regenerating would produce "-dev.0", which is a lie — keep the source stamp instead.
if git -C "$DEST" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ( cd "$DEST" && python3 -c "import sys; sys.path.insert(0, '.'); from video_studio import build_info; build_info.generate()" )
else
  echo "  (installed copy has no git history — keeping the source stamp)"
fi

echo -n "stamp: "
python3 - "$DEST" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1]) / "BUILD_INFO.json"
print(json.loads(p.read_text()).get("version", "missing") if p.is_file() else "missing")
PY

# restart the loopback studio so new backend code is actually loaded
STATE="$HOME/.hermes/plugin-data/video-studio/studio-host.json"
if [ -f "$STATE" ]; then
  OLD_PID="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('pid',''))" "$STATE" 2>/dev/null || true)"
  [ -n "${OLD_PID:-}" ] && kill "$OLD_PID" 2>/dev/null || true
  sleep 1
fi
( cd "$DEST" && nohup python3 -m video_studio.host_launcher >/dev/null 2>&1 & ) || true
sleep 2
python3 - "$STATE" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
print("host:", json.loads(p.read_text()) if p.is_file() else "not started")
PY
