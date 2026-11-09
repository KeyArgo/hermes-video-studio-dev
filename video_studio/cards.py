#!/usr/bin/env python3
"""Card renderer — title / limit / end, in the Hewn brand grammar.

Brand grammar (from ~/Vaults/hackathons/2026-11-11-brand-and-seven-videos.md):
  bg #0b0d10 · one accent per product · Inter + JetBrains Mono · no gradients · restraint.

Usage:
  cards.py <spec.json> <outdir>        # writes title.png end.png limit.png
"""
import json, os, subprocess, sys, html, tempfile, shutil

ACCENT_DEFAULT = "#3b82f6"
BG = "#0b0d10"
INK = "#e8eaed"
DIM = "#8b929c"

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:%(bg)s;overflow:hidden}
body{font-family:Inter,'DejaVu Sans',system-ui,sans-serif;color:%(ink)s}
.stage{width:1920px;height:1080px;padding:140px;display:flex;flex-direction:column;justify-content:space-between}
.rule{position:absolute;left:140px;top:0;width:6px;height:0;background:%(ac)s}
.top{display:flex;justify-content:space-between;align-items:baseline;
     font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:22px;color:%(dim)s;letter-spacing:.14em;text-transform:uppercase}
.mark{color:%(ac)s}
.mid h1{font-size:150px;line-height:.95;font-weight:600;letter-spacing:-.04em}
.mid .sub{margin-top:28px;font-size:40px;color:%(dim)s;font-weight:400;letter-spacing:-.01em;max-width:1300px}
.mid .claim{margin-top:56px;font-size:34px;line-height:1.45;color:%(ink)s;max-width:1250px;opacity:.86}
.bot{display:flex;justify-content:space-between;align-items:flex-end;
     font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:24px;color:%(dim)s}
.cmd{color:%(ink)s;font-size:30px}
.cmd .prompt{color:%(ac)s;margin-right:18px}
.bar{position:absolute;left:0;bottom:0;height:5px;width:1920px;background:%(ac)s;opacity:.85}
.barfill{position:absolute;left:0;bottom:0;height:5px;background:%(ac)s}
.limit .label{font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:22px;
  letter-spacing:.18em;text-transform:uppercase;color:%(ac)s}
.limit .body{margin-top:56px;font-size:78px;line-height:1.22;font-weight:500;letter-spacing:-.02em;max-width:1450px}
.limit .note{margin-top:48px;font-size:30px;color:%(dim)s;font-family:'JetBrains Mono','DejaVu Sans Mono',monospace}
"""


def page(kind, s):
    ac = s.get("accent", ACCENT_DEFAULT)
    title = html.escape(s.get("title", ""))
    sub = html.escape(s.get("subtitle", ""))
    claim = html.escape(s.get("claim", ""))
    install = html.escape(s.get("install", ""))
    limit = html.escape(s.get("limit", ""))
    studio = html.escape(s.get("studio", "Hewn"))
    foot = html.escape(s.get("footnote", "Plugin Hackathon · Nov 2026"))

    if kind == "title":
        mid = f'<div class="mid"><h1>{title}</h1><div class="sub">{sub}</div><div class="claim">{claim}</div></div>'
        bot = f'<div class="bot"><span class="mark">{studio}</span><span>{foot}</span></div>'
    elif kind == "end":
        # An empty install line renders a bare prompt, which looks like a bug.
        # No command, no prompt.
        cmd = (f'<div class="cmd" style="margin-top:64px">'
               f'<span class="prompt">$</span>{install}</div>') if install.strip() else ""
        mid = f'<div class="mid"><h1 style="font-size:96px">{title}</h1>{cmd}</div>'
        bot = f'<div class="bot"><span class="mark">{studio}</span><span>{foot}</span></div>'
    elif kind == "limit":
        mid = (f'<div class="mid limit"><div class="label">what it does not do</div>'
               f'<div class="body">{limit}</div><div class="note">{title} · LIMITS.md</div></div>')
        bot = f'<div class="bot"><span class="mark">{studio}</span><span>{foot}</span></div>'
    else:
        raise SystemExit(f"unknown card kind {kind}")

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS % dict(bg=BG,ink=INK,dim=DIM,ac=ac)}</style></head>
<body><div class="rule"></div><div class="stage">{mid}{bot}</div><div class="bar"></div></body></html>"""


def render(spec, kind, outdir):
    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=outdir)
    src = os.path.join(tmp, "card.html")
    png = os.path.join(outdir, f"{kind}.png")
    with open(src, "w") as f:
        f.write(page(kind, spec))
    cmd = ["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--window-size=1920,1080",
           f"--screenshot={png}", f"file://{src}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.exists(png):
        raise SystemExit(f"chrome failed for {kind}: {r.stderr[-800:]}")
    return png


def render_card(out_png: str, kind: str = "title", *, title: str, subtitle: str = "",
                claim: str = "", install: str = "", limit: str = "",
                accent: str = ACCENT_DEFAULT, studio: str = "Hewn") -> str:
    """Render one card to out_png and return the path.

    kind: 'title' | 'limit' | 'end'. Raises ValueError for anything else and
    RuntimeError if Chrome fails to produce the image.
    """
    if kind not in ("title", "limit", "end"):
        raise ValueError(f"unknown card kind {kind!r}")
    spec = {"title": title, "subtitle": subtitle, "claim": claim,
            "install": install, "limit": limit, "accent": accent,
            "studio": studio}
    out_png = os.path.abspath(out_png)
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="hvs-card-")
    try:
        png = render(spec, kind, tmp)
        if png != out_png:
            shutil.move(png, out_png)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.exists(out_png):
        raise RuntimeError("chrome produced no image")
    return out_png
