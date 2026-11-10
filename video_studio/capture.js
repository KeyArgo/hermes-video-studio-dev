// Scripted screen capture of a web app, for demo footage of a plugin's real interface.
//
// Why this and not a desktop recorder: on this host the session is Wayland/KDE with no
// wf-recorder, grim or xdg-desktop-portal, and Electron windows capture as black. A browser
// recording of the app itself is clean, 1080p, reproducible, and shows nothing but the app —
// no other sessions, no notifications.
//
// A synthetic cursor is drawn and animated to each target before clicking, because a recording
// without a pointer looks like a slideshow rather than someone using the thing.
const { chromium } = require("/home/dev/.hermes/hermes-agent/node_modules/playwright");

const CURSOR = `
  (() => {
    if (document.getElementById('__cap_cursor')) return;
    const c = document.createElement('div');
    c.id = '__cap_cursor';
    c.style.cssText = 'position:fixed;left:0;top:0;width:18px;height:18px;border-radius:50%;' +
      'background:rgba(255,255,255,.92);box-shadow:0 0 0 2px rgba(0,0,0,.55),0 2px 10px rgba(0,0,0,.45);' +
      'pointer-events:none;z-index:2147483647;transform:translate(-50%,-50%);' +
      'transition:left .45s cubic-bezier(.22,.61,.36,1),top .45s cubic-bezier(.22,.61,.36,1);';
    document.body.appendChild(c);
  })();
`;

async function moveCursor(page, x, y) {
  await page.evaluate(([x, y]) => {
    const c = document.getElementById('__cap_cursor');
    if (c) { c.style.left = x + 'px'; c.style.top = y + 'px'; }
  }, [x, y]);
  await page.waitForTimeout(480);
}

async function run(spec) {
  const { url, steps = [], out, width = 1920, height = 1080 } = spec;
  const browser = await chromium.launch({ args: ["--no-sandbox", "--force-device-scale-factor=1",
                       "--autoplay-policy=no-user-gesture-required"] });
  const ctx = await browser.newContext({
    viewport: { width, height },
    recordVideo: { dir: out, size: { width, height } },
  });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 200)); });

  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  await page.evaluate(CURSOR);
  await moveCursor(page, Math.round(width * 0.5), Math.round(height * 0.55));

  for (const step of steps) {
    if (step.wait) { await page.waitForTimeout(step.wait); continue; }
    if (step.mouse) { await moveCursor(page, step.mouse[0], step.mouse[1]); continue; }
    if (step.scroll) { await page.mouse.wheel(0, step.scroll); await page.waitForTimeout(500); continue; }
    if (step.eval) { await page.evaluate(step.eval); await page.waitForTimeout(step.after || 600); continue; }
    if (step.drive) {
      // Feed a real envelope into a page that expects its host to push values: Aura's
      // visualizer reads CSS variables the desktop shell normally sets.
      await page.evaluate((d) => {
        const root = document.documentElement;
        d.vars.forEach((v) => root.style.setProperty(v.name, String(v.start ?? 0)));
        const t0 = performance.now();
        const frame = () => {
          const ms = performance.now() - t0;
          const i = Math.min(d.values.length - 1, Math.floor(ms / d.ms));
          d.vars.forEach((v) => {
            const arr = v.values || d.values;
            const lo = v.min ?? 0, hi = v.high ?? 1;
            const val = lo + (hi - lo) * (arr[i] ?? 0);
            root.style.setProperty(v.name, val.toFixed(4));
          });
          if (ms < d.ms * d.values.length) requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
      }, { values: step.drive.values, ms: step.drive.ms || 33, vars: step.drive.vars });
      await page.waitForTimeout(step.drive.values.length * (step.drive.ms || 33) + 300);
      continue;
    }
    if (step.click) {
      const el = await page.$(step.click);
      if (!el) { console.error("missing selector: " + step.click); continue; }
      const box = await el.boundingBox();
      if (box) await moveCursor(page, box.x + box.width / 2, box.y + box.height / 2);
      await el.click({ timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(step.after || 900);
      continue;
    }
    if (step.type) {
      const el = await page.$(step.type.selector);
      if (!el) { console.error("missing selector: " + step.type.selector); continue; }
      const box = await el.boundingBox();
      if (box) await moveCursor(page, box.x + box.width / 2, box.y + box.height / 2);
      await el.click({ timeout: 8000 }).catch(() => {});
      await page.keyboard.type(step.type.text || "", { delay: 45 });
      await page.waitForTimeout(step.after || 600);
      continue;
    }
  }

  await page.waitForTimeout(700);
  const video = page.video();
  await ctx.close();
  await browser.close();
  const path = await video.path();
  console.log(JSON.stringify({ video: path, consoleErrors }));
}

const spec = JSON.parse(process.argv[2]);
run(spec).catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
