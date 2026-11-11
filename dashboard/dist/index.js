// Hermes Video Studio — dashboard tab (plain IIFE, no build step).
// Backend: /api/plugins/video-studio/* (dashboard/plugin_api.py).
//
// The UI is vanilla DOM inside a single React component, so the same file runs
// in the dashboard tab and as a standalone page. Everything shown is a field the
// API returned: the UI never invents a duration, a check or a segment.
;(function () {
  'use strict'

  var PLUGIN = 'video-studio'
  var API = '/api/plugins/' + PLUGIN
  var TITLE = 'Video Studio'

  // ------------------------------------------------------------------ utils
  function el(tag, attrs, kids) {
    var n = document.createElement(tag)
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k]
      if (v == null || v === false) return
      if (k === 'class') n.className = v
      else if (k === 'text') n.textContent = v
      else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v)
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') n.addEventListener(k.slice(2), v)
      else n.setAttribute(k, v)
    })
    ;(kids || []).forEach(function (k) {
      if (k == null || k === false) return
      n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k)
    })
    return n
  }

  function fmt(t) {
    if (t == null || isNaN(t)) return '—'
    var m = Math.floor(t / 60), s = t - m * 60
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1)
  }
  function pct(t, total) { return Math.max(0, Math.min(100, (t / (total || 1)) * 100)) }
  function short(n) { return n >= 1024 ? (n / 1024).toFixed(1) + 'k' : String(n) }

  // Copies only when the clipboard actually took the text: the chip's "copied" state
  // is a claim about what happened, not a decoration.
  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text)
        return true
      }
    } catch (e) { /* fall through to the textarea */ }
    try {
      var area = document.createElement('textarea')
      area.value = text
      area.setAttribute('readonly', '')
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      var ok = document.execCommand('copy')
      area.remove()
      return ok
    } catch (e) { return false }
  }

  function api(path, opts) {
    return fetch(API + path, Object.assign({
      headers: { 'Content-Type': 'application/json' }
    }, opts || {})).then(function (r) {
      return r.text().then(function (txt) {
        var body
        try { body = txt ? JSON.parse(txt) : {} } catch (e) { body = { detail: txt } }
        if (!r.ok) throw new Error(body.detail || body.message || ('HTTP ' + r.status))
        return body
      })
    })
  }

  // -------------------------------------------------------------------- css
  var CSS = [
    '.hvs{--bg:#0b0d10;--panel:#111419;--panel2:#151920;--line:#20242d;--ink:#e8eaed;--dim:#8b929c;--accent:#3b82f6;',
    '  position:absolute;inset:0;display:grid;grid-template-rows:48px 1fr;background:var(--bg);color:var(--ink);',
    '  font-family:Inter,system-ui,-apple-system,sans-serif;font-size:13px;overflow:hidden}',
    '.hvs *{box-sizing:border-box}',
    '.hvs-top{display:flex;align-items:center;gap:14px;padding:0 16px;border-bottom:1px solid var(--line);background:var(--panel)}',
    '.hvs-brand{font-weight:600;letter-spacing:-.01em}',
    '.hvs-brand i{font-style:normal;color:var(--accent)}',
    '.hvs-tag{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim);',
    '  text-transform:uppercase;letter-spacing:.14em}',
    '.hvs-spacer{flex:1}',
    '.hvs-ver{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim);',
    '  border:1px solid var(--line);border-radius:3px;padding:2px 7px;cursor:copy;white-space:nowrap;letter-spacing:0}',
    '.hvs-ver:hover{color:var(--ink);border-color:#2c333f}',
    '.hvs-ver.dirty{color:#f0a13a;border-color:#4a3a1c}',
    '.hvs-ver[data-copied="1"]{color:#34d399;border-color:#1f4a38}',
    '.hvs-ver[data-copied="1"]::after{content:" · copied"}',
    '.hvs-ver[data-copied="failed"]{color:#fca5a5;border-color:#4a2222}',
    '.hvs-ver[data-copied="failed"]::after{content:" · copy failed"}',
    '.hvs-body{display:grid;grid-template-columns:252px minmax(0,1fr) 312px;min-height:0}',
    '.hvs-rail,.hvs-insp{background:var(--panel);border-right:1px solid var(--line);overflow:auto;min-height:0;padding:2px 0}',
    '.hvs-insp{border-right:0;border-left:1px solid var(--line)}',
    '.hvs-sec{padding:12px 14px;border-bottom:1px solid var(--line)}',
    '.hvs-h{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:10px;letter-spacing:.16em;',
    '  text-transform:uppercase;color:var(--dim);margin:0 0 10px}',
    '.hvs-proj{display:block;width:100%;text-align:left;background:none;border:0;border-left:2px solid transparent;',
    '  color:var(--ink);padding:9px 12px;cursor:pointer;font:inherit}',
    '.hvs-proj:hover{background:var(--panel2)}',
    '.hvs-proj.on{background:var(--panel2);border-left-color:var(--accent)}',
    '.hvs-proj b{display:block;font-weight:500;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.hvs-proj span{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim)}',
    '.hvs-stage{display:flex;flex-direction:column;min-width:0;min-height:0}',
    '.hvs-head{display:flex;align-items:baseline;gap:12px;padding:14px 20px 10px}',
    '.hvs-head h1{font-size:20px;font-weight:600;letter-spacing:-.02em;margin:0}',
    '.hvs-head .meta{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim)}',
    '.hvs-head .meta b{color:var(--ink);font-weight:500}',
    '.hvs-player{flex:1;min-height:0;background:#000;margin:0 20px;border:1px solid var(--line);display:flex;',
    '  align-items:center;justify-content:center;overflow:hidden;position:relative}',
    '.hvs-player video{max-width:100%;max-height:100%;display:block}',
    '.hvs-player .empty{color:var(--dim);font-size:12px;padding:24px;text-align:center;line-height:1.6}',
    '.hvs-wave{margin:12px 20px 0;position:relative;height:74px;border:1px solid var(--line);background:var(--panel2);',
    '  cursor:crosshair;user-select:none;overflow:hidden}',
    '.hvs-wave img{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;opacity:.85;pointer-events:none}',
    '.hvs-band{position:absolute;top:0;bottom:0;background:rgba(139,146,156,.10);',
    '  border-left:1px dashed rgba(139,146,156,.45);cursor:pointer}',
    '.hvs-band:hover{background:rgba(59,130,246,.24);border-left-color:var(--accent)}',
    '.hvs-sel{position:absolute;top:0;bottom:0;background:rgba(59,130,246,.22);border-left:2px solid var(--accent);',
    '  border-right:2px solid var(--accent);pointer-events:none}',
    '.hvs-ruler{display:flex;justify-content:space-between;margin:4px 20px 0;',
    '  font-family:"JetBrains Mono",ui-monospace,monospace;font-size:10px;color:var(--dim)}',
    '.hvs-tl{margin:12px 20px 0;display:flex;gap:2px;height:26px}',
    '.hvs-tl div{background:var(--panel2);border:1px solid var(--line);border-radius:2px;position:relative;',
    '  min-width:2px;transition:background .18s cubic-bezier(.2,.8,.2,1)}',
    '.hvs-tl div:hover{background:#1b2029}',
    '.hvs-tl div.muted{background:repeating-linear-gradient(45deg,#14181f,#14181f 4px,#1b2029 4px,#1b2029 8px)}',
    '.hvs-tl div.card{background:#1a2536;border-color:#2b3d5c;display:flex;align-items:center;justify-content:center}',
    '.hvs-tl div.card .g{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:10px;color:var(--accent)}',
    '.hvs-bar{display:flex;gap:8px;padding:12px 20px;align-items:center;flex-wrap:wrap}',
    '.hvs-btn{font:inherit;font-size:12px;background:var(--panel2);color:var(--ink);border:1px solid var(--line);',
    '  padding:7px 12px;border-radius:3px;cursor:pointer;transition:background .18s cubic-bezier(.2,.8,.2,1)}',
    '.hvs-btn:hover:not(:disabled){background:#1e242e}',
    '.hvs-btn:disabled{opacity:.45;cursor:not-allowed}',
    '.hvs-btn.primary{background:var(--accent);border-color:var(--accent);color:#06101f;font-weight:600}',
    '.hvs-btn.danger{border-color:#4a2222;color:#fca5a5}',
    '.hvs-btn.sm{padding:4px 9px;font-size:11px}',
    '.hvs-note{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim)}',
    '.hvs-note b{color:var(--ink);font-weight:500}',
    '.hvs-check{display:flex;gap:8px;align-items:baseline;padding:5px 0;font-size:12px}',
    '.hvs-dot{width:6px;height:6px;border-radius:50%;flex:0 0 6px;margin-top:5px}',
    '.hvs-dot.ok{background:#10b981}.hvs-dot.no{background:#ef4444}.hvs-dot.na{background:#4b5563}',
    '.hvs-check .t{flex:1}.hvs-check .d{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim)}',
    '.hvs-op{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim);padding:4px 0;',
    '  border-bottom:1px dashed #1a1f27;display:flex;gap:8px}',
    '.hvs-op b{color:var(--ink);font-weight:500}',
    '.hvs-err{background:#2a1416;border:1px solid #4a2222;color:#fca5a5;padding:8px 10px;border-radius:3px;',
    '  margin:8px 20px;font-size:12px}',
    '.hvs-inp{width:100%;background:#0d1015;border:1px solid var(--line);color:var(--ink);font:inherit;',
    '  font-size:12px;padding:7px 8px;border-radius:3px}',
    '.hvs-inp::placeholder{color:#5b6472}',
    '.hvs-renders{max-height:132px;overflow:auto}',
    '.hvs-pill{display:inline-block;font-family:"JetBrains Mono",ui-monospace,monospace;font-size:10px;',
    '  padding:2px 6px;border:1px solid var(--line);border-radius:2px;color:var(--dim);margin-right:4px}',
    '.hvs-empty{padding:24px 16px;color:var(--dim);font-size:12px;line-height:1.7}',
    '.hvs-x{background:none;border:0;color:#5b6472;font:inherit;font-size:14px;cursor:pointer;padding:0 2px}',
    '.hvs-x:hover{color:#fca5a5}'
  ].join('\n')

  function ensureCss(doc) {
    if (doc.getElementById('hvs-css')) return
    var s = el('style', { id: 'hvs-css' })
    s.textContent = CSS
    doc.head.appendChild(s)
  }

  // ------------------------------------------------------------------- app
  function mount(host) {
    ensureCss(host.ownerDocument || document)
    var root = el('div', { class: 'hvs' })
    host.appendChild(root)

    var state = { projects: [], id: null, view: null, analyze: null, sel: null, busy: '', error: '', notice: '', mode: 'cut', ver: null }

    function redraw() {
      root.innerHTML = ''
      root.appendChild(renderTop())
      if (state.mode === 'films') root.appendChild(renderFilms())
      else root.appendChild(renderBody())
    }

    // The build this tab is running: /version -> BUILD_INFO.json -> live git -> plugin.yaml.
    // Read at runtime; a version typed into this file would drift from the code answering.
    function loadVersion() {
      return api('/version').then(function (r) {
        state.ver = r.build || { version: r.version }
        redraw()
      }).catch(function () { /* no guess beats a wrong number */ })
    }

    function versionChip() {
      var b = state.ver
      if (!b) return el('span', { class: 'hvs-ver hvs-tag', text: 'v…' })
      var parts = ['v' + (b.version || 'unknown')]
      if (b.commit_date) parts.push(String(b.commit_date).slice(0, 10))
      if (b.commit_short || b.commit) parts.push((b.commit_short || b.commit).slice(0, 8))
      var chip = el('span', {
        class: 'hvs-ver' + (b.dirty ? ' dirty' : ''),
        text: parts.join(' · ') + (b.dirty ? ' · modified' : ''),
        title: [
          'Video Studio ' + (b.version || 'unknown'),
          b.commit ? 'commit ' + b.commit : 'no commit (installed copy)',
          b.commit_date ? 'committed ' + b.commit_date : '',
          b.built_at ? 'installed ' + b.built_at : '',
          b.channel ? 'channel ' + b.channel : '',
          b.source ? 'read from ' + b.source : '',
          'click to copy this line'
        ].filter(Boolean).join('\n')
      })
      chip.addEventListener('click', async function () {
        var copied = await copyText('Video Studio ' + chip.textContent.trim())
        chip.dataset.copied = copied ? '1' : 'failed'
        setTimeout(function () { delete chip.dataset.copied }, 1400)
      })
      return chip
    }

    // ---------------- top bar
    function renderTop() {
      var brandName = state.view ? state.view.title : TITLE
      return el('div', { class: 'hvs-top' }, [
        el('div', { class: 'hvs-brand' }, ['Video Studio ', el('i', null, ['Cut'])]),
        el('div', { class: 'hvs-tag', text: brandName }),
        el('div', { class: 'hvs-spacer' }),
        el('button', {
          class: 'hvs-btn', style: { padding: '5px 12px' },
          text: state.mode === 'films' ? 'Cutting room' : 'Films',
          onclick: function () {
            state.mode = state.mode === 'films' ? 'cut' : 'films'
            if (state.mode === 'films' && !F.list.length) loadFilms()
            redraw()
          }
        }),
        state.mode === 'cut' && state.view ? el('div', { class: 'hvs-tag', text: state.view.segments.length + ' segments · ' + state.view.captions.length + ' captions' }) : null,
        state.busy ? el('div', { class: 'hvs-tag', text: state.busy + '…' }) : null,
        versionChip()
      ])
    }


    // ---------------------------------------------------------------- films
    // A film is films/<id>.json in the motion workspace: beats -> one HTML
    // shot each. Same rule as the rest of the tab: every number shown came
    // back from the API.
    var F = { list: [], id: null, film: null, gate: null, job: null, shot: null, note: '', err: '' }

    function loadFilms() {
      return api('/films').then(function (r) {
        F.list = r.films || []
        if (!F.id && F.list.length) return openFilm(F.list[0].id)
        redraw()
      }).catch(function (e) { F.err = e.message; redraw() })
    }

    function openFilm(id) {
      F.id = id; F.gate = null; F.shot = null; F.job = null
      return api('/films/' + encodeURIComponent(id)).then(function (r) {
        F.film = r; redraw()
      }).catch(function (e) { F.err = e.message; redraw() })
    }

    function pollJob(jid) {
      F.job = { state: 'running', tail: '' }
      redraw()
      var tick = function () {
        api('/jobs/' + jid).then(function (j) {
          F.job = j
          if (j.state === 'running') { redraw(); setTimeout(tick, 2500) }
          else {
            if (j.state === 'done') {
              F.note = j.kind === 'shot' ? 'Shot rendered in ' + j.seconds + 's'
                    : 'Film rendered in ' + j.seconds + 's'
              if (F.film) openFilm(F.id)
            } else {
              F.err = 'Render failed (rc ' + j.rc + '). ' + (j.tail || '').slice(-400)
            }
            redraw()
          }
        }).catch(function (e) { F.err = e.message; redraw() })
      }
      setTimeout(tick, 1200)
    }

    function renderWhole() {
      F.err = ''; F.note = ''
      api('/films/' + encodeURIComponent(F.id) + '/render', { method: 'POST' })
        .then(function (r) { pollJob(r.job) })
        .catch(function (e) { F.err = e.message; redraw() })
    }

    function renderShot(bid) {
      F.err = ''; F.note = ''
      api('/films/' + encodeURIComponent(F.id) + '/beats/' + encodeURIComponent(bid) + '/render', { method: 'POST' })
        .then(function (r) { pollJob(r.job) })
        .catch(function (e) { F.err = e.message; redraw() })
    }

    function runGate() {
      F.err = ''; F.note = 'Running gate…'; redraw()
      api('/films/' + encodeURIComponent(F.id) + '/gate', { method: 'POST' })
        .then(function (r) { F.gate = r; F.note = ''; redraw() })
        .catch(function (e) { F.gate = null; F.note = ''; F.err = e.message; redraw() })
    }

    function editShot(bid) {
      F.shot = { id: bid, source: 'loading…' }; redraw()
      api('/films/' + encodeURIComponent(F.id) + '/beats/' + encodeURIComponent(bid) + '/source')
        .then(function (r) { F.shot = { id: bid, source: r.source, path: r.path }; redraw() })
        .catch(function (e) { F.shot = null; F.err = e.message; redraw() })
    }

    function saveShot(text) {
      if (!F.shot) return
      api('/films/' + encodeURIComponent(F.id) + '/beats/' + encodeURIComponent(F.shot.id) + '/source', {
        method: 'PUT', body: JSON.stringify({ source: text })
      }).then(function () { F.note = 'Shot saved — re-render it to see the change'; F.shot = null; openFilm(F.id) })
        .catch(function (e) { F.err = e.message; redraw() })
    }

    function renderFilms() {
      var film = F.film
      var rail = el('div', { class: 'hvs-rail' }, [
        el('div', { class: 'hvs-sec' }, [
          el('p', { class: 'hvs-h', text: 'Films' })
        ].concat(F.list.map(function (f) {
          return el('button', {
            class: 'hvs-proj' + (f.id === F.id ? ' on' : ''), onclick: function () { openFilm(f.id) }
          }, [el('b', { text: f.title }),
              el('span', { text: fmt(f.total) + ' · ' + f.beats + ' shots · ' + f.renders.length + ' renders' })])
        })))
      ])

      var stage = el('div', { class: 'hvs-stage' })
      var head = el('div', { class: 'hvs-head' }, [
        el('h1', { text: film ? film.title : 'Films' }),
        el('div', { class: 'hvs-tag', text: film ? fmt(film.total) + ' · ' + film.beats.length + ' shots' : 'loading' }),
        el('div', { class: 'hvs-spacer' }),
        film ? el('button', { class: 'hvs-btn', text: 'Render film', onclick: renderWhole, style: { padding: '5px 12px' } }) : null,
        film ? el('button', { class: 'hvs-btn', text: 'Gate', onclick: runGate, style: { padding: '5px 12px', marginLeft: '8px' } }) : null
      ])
      stage.appendChild(head)

      var player = el('div', { class: 'hvs-player' })
      if (film) {
        var hasRender = (film.renders || []).length > 0
        if (hasRender) {
          player.appendChild(el('video', {
            src: API + '/films/' + encodeURIComponent(film.id) + '/video?t=' + Date.now(),
            controls: 'controls', style: { width: '100%', height: '100%', objectFit: 'contain' }
          }))
        } else {
          player.appendChild(el('div', { class: 'empty', text: 'No render yet. Edit the shots, then Render film.' }))
        }
      }
      stage.appendChild(player)

      if (film) {
        var strip = el('div', { class: 'hvs-beats', style: {
          display: 'flex', gap: '10px', overflowX: 'auto', padding: '12px 20px 4px'
        } }, film.beats.map(function (b, i) {
          return el('div', { style: {
            minWidth: '168px', border: '1px solid var(--line)', borderRadius: '8px',
            padding: '10px', background: 'var(--panel2)', opacity: b.exists ? '1' : '.5'
          } }, [
            el('div', { class: 'hvs-tag', text: (i + 1) + ' · ' + b.id.replace(/^shot-/, '') }),
            el('div', { style: { fontSize: '15px', margin: '6px 0' }, text: b.duration.toFixed(1) + 's' }),
            el('div', { class: 'hvs-tag', text: b.shot_render ? 'shot rendered' : 'not rendered' }),
            el('button', { class: 'hvs-btn', text: 'Re-render', style: { marginTop: '8px', padding: '3px 8px', fontSize: '11px' },
              onclick: function () { renderShot(b.id) } }),
            el('button', { class: 'hvs-btn', text: 'Edit', style: { marginTop: '6px', marginLeft: '6px', padding: '3px 8px', fontSize: '11px' },
              onclick: function () { editShot(b.id) } })
          ])
        }))
        stage.appendChild(strip)
      }

      var insp = el('div', { class: 'hvs-insp' })
      if (F.note) insp.appendChild(el('div', { class: 'hvs-sec', style: { color: 'var(--accent)' }, text: F.note }))
      if (F.err) insp.appendChild(el('div', { class: 'hvs-sec', style: { color: '#ef4444', whiteSpace: 'pre-wrap' }, text: F.err }))

      if (F.job) {
        insp.appendChild(el('div', { class: 'hvs-sec' }, [
          el('p', { class: 'hvs-h', text: 'Render' }),
          el('div', { text: F.job.state + (F.job.seconds ? ' · ' + F.job.seconds + 's' : '') })
        ]))
      }

      if (F.gate) {
        insp.appendChild(el('div', { class: 'hvs-sec' }, [
          el('p', { class: 'hvs-h', text: 'Gate · ' + F.gate.video }),
          el('div', { style: { fontWeight: '600', color: F.gate.shippable ? '#22c55e' : '#ef4444', marginBottom: '8px' },
            text: F.gate.shippable ? 'shippable' : 'failed: ' + (F.gate.failed || []).join(', ') })
        ].concat((F.gate.checks || []).map(function (c) {
          return el('div', { style: { fontSize: '12px', marginBottom: '6px', lineHeight: '1.4' } }, [
            el('span', { style: { color: c.pass ? '#22c55e' : '#ef4444' }, text: (c.pass ? '✓ ' : '✗ ') }),
            el('span', { text: c.check + ' — ' + c.detail })
          ])
        }))))
      }

      if (F.shot) {
        var ta = el('textarea', {
          spellcheck: 'false',
          style: { width: '100%', height: '320px', fontFamily: 'var(--font-mono, monospace)', fontSize: '11px',
                   background: '#0d1015', color: '#e8eaed', border: '1px solid var(--line)', borderRadius: '6px', padding: '8px' },
          text: F.shot.source
        })
        insp.appendChild(el('div', { class: 'hvs-sec' }, [
          el('p', { class: 'hvs-h', text: 'Shot · ' + F.shot.id }),
          ta,
          el('button', { class: 'hvs-btn', text: 'Save shot', style: { marginTop: '8px', width: '100%' },
            onclick: function () { saveShot(ta.value) } })
        ]))
      }

      if (!F.gate && !F.shot) {
        insp.appendChild(el('div', { class: 'hvs-sec' }, [
          el('p', { class: 'hvs-h', text: 'Checks' }),
          el('div', { style: { color: 'var(--dim)', lineHeight: '1.6', fontSize: '12px' },
            text: 'Gate runs hook, motion and legibility on the latest render. A film only ships when all three pass.' })
        ]))
      }

      return el('div', { class: 'hvs-body' }, [rail, stage, insp])
    }

    // ---------------- body
    function renderBody() {
      return el('div', { class: 'hvs-body' }, [renderRail(), renderStage(), renderInspector()])
    }

    function renderRail() {
      var list = el('div', null, state.projects.map(function (p) {
        return el('button', {
          class: 'hvs-proj' + (p.id === state.id ? ' on' : ''),
          onclick: function () { open(p.id) }
        }, [
          el('b', { text: p.title }),
          el('span', null, [fmt(p.duration) + ' · ' + p.ops + ' ops' + (p.entry ? ' · ' + p.entry : '')])
        ])
      }))
      if (!state.projects.length) {
        list.appendChild(el('div', { class: 'hvs-empty', text: 'No projects yet. Point at a clip below and the first cut opens here.' }))
      }

      var path = el('input', { class: 'hvs-inp', placeholder: '/path/to/clip.mp4', spellcheck: 'false' })
      var entry = el('input', { class: 'hvs-inp', placeholder: 'entry (optional)', spellcheck: 'false', style: { marginTop: '6px' } })
      var go = el('button', {
        class: 'hvs-btn', style: { marginTop: '6px', width: '100%' }, text: 'Open clip',
        onclick: function () {
          var src = path.value.trim()
          if (!src) return
          busy('opening', function () {
            return api('/projects', {
              method: 'POST',
              body: JSON.stringify({ source: src, entry: entry.value.trim() })
            }).then(function (r) { return loadProjects().then(function () { return open(r.id) }) })
          })
        }
      })

      return el('div', { class: 'hvs-rail' }, [
        el('div', { class: 'hvs-sec' }, [el('h3', { class: 'hvs-h', text: 'Cuts' }), list]),
        el('div', { class: 'hvs-sec' }, [el('h3', { class: 'hvs-h', text: 'New from clip' }), path, entry, go])
      ])
    }

    function renderStage() {
      if (!state.view) {
        return el('div', { class: 'hvs-stage' }, [
          el('div', { class: 'hvs-player', style: { marginTop: '20px' } }, [
            el('div', { class: 'empty', html: '' }, [
              el('div', { text: 'Video Studio' }),
              el('div', { style: { marginTop: '8px', color: '#5b6472' }, text: 'Open a clip from the left. The agent can cut it too — it uses the same timeline you see here.' })
            ])
          ])
        ])
      }
      var v = state.view
      var total = v.source_duration

      // player: latest cut if one exists, else the source
      var latest = v.renders && v.renders.length ? v.renders[0].name : null
      var playing = state.playing || (latest ? 'render' : 'source')
      var src = playing === 'render' && latest
        ? API + '/projects/' + v.id + '/renders/' + encodeURIComponent(latest)
        : API + '/projects/' + v.id + '/source'

      var video = el('video', {
        src: src + (playing === 'render' ? '?t=' + Date.now() : ''),
        poster: API + '/projects/' + v.id + '/poster.png?t=' + Date.now(),
        controls: 'controls', preload: 'metadata', playsinline: 'playsinline',
        style: { width: '100%', height: '100%', objectFit: 'contain' }
      })
      ;['loadedmetadata', 'loadeddata'].forEach(function (evt) {
        video.addEventListener(evt, function () {
          if (video.readyState >= 1 && !video.currentTime) video.currentTime = 0.1
        })
      })
      video.crossOrigin = 'anonymous'

      // waveform + selection
      var wave = el('div', { class: 'hvs-wave' })
      wave.appendChild(el('img', {
        src: API + '/projects/' + v.id + '/waveform.png?w=1600&h=160&t=' + Date.now(),
        alt: 'waveform'
      }))

      var silences = (state.analyze && state.analyze.silences) || []
      silences.forEach(function (s) {
        var a = s[0], b = s[1]
        wave.appendChild(el('div', {
          class: 'hvs-band',
          title: 'pause ' + fmt(a) + ' – ' + fmt(b),
          style: { left: pct(a, total) + '%', width: (pct(b, total) - pct(a, total)) + '%' },
          onclick: function (e) {
            e.stopPropagation()
            state.sel = { a: a + 0.12, b: Math.max(a + 0.3, b - 0.12) }
            redraw()
          }
        }))
      })

      if (state.sel) {
        wave.appendChild(el('div', {
          class: 'hvs-sel',
          style: {
            left: pct(state.sel.a, total) + '%',
            width: Math.max(0.4, pct(state.sel.b, total) - pct(state.sel.a, total)) + '%'
          }
        }))
      }

      // drag to select
      var drag = null
      function timeAt(ev) {
        var r = wave.getBoundingClientRect()
        return Math.max(0, Math.min(total, ((ev.clientX - r.left) / r.width) * total))
      }
      wave.addEventListener('mousedown', function (ev) {
        drag = timeAt(ev)
        state.sel = { a: drag, b: drag }
        redraw()
      })
      wave.addEventListener('mousemove', function (ev) {
        if (drag == null) return
        var t = timeAt(ev)
        state.sel = { a: Math.min(drag, t), b: Math.max(drag, t) }
        redraw()
      })
      window.addEventListener('mouseup', function () { drag = null }, { once: true })

      // timeline blocks
      var tl = el('div', { class: 'hvs-tl' }, v.segments.map(function (s, i) {
        var isCard = s.kind === 'card'
        return el('div', {
          class: (s.muted ? 'muted ' : '') + (isCard ? 'card' : ''),
          style: { flex: Math.max(1, s.duration) + ' 1 0' },
          title: isCard
            ? 'card ' + (s.png || '') + '  ' + s.duration.toFixed(2) + 's  (atomic)'
            : '#' + (i + 1) + '  ' + fmt(s.src_in) + '–' + fmt(s.src_out) +
              '  (' + s.duration.toFixed(2) + 's' + (s.speed !== 1 ? ', ' + s.speed + '×' : '') + ')'
        }, isCard ? [el('span', { class: 'g', text: 'C' })] : [])
      }))

      var selNote = state.sel
        ? el('span', { class: 'hvs-note' }, ['selection ', el('b', { text: fmt(state.sel.a) + ' – ' + fmt(state.sel.b) }),
            ' (' + (state.sel.b - state.sel.a).toFixed(2) + 's)'])
        : el('span', { class: 'hvs-note', text: 'drag the waveform to select a cut' })

      var bar = el('div', { class: 'hvs-bar' }, [
        el('button', {
          class: 'hvs-btn danger', text: 'Snip selection',
          disabled: !state.sel || state.sel.b - state.sel.a < 0.05,
          onclick: function () {
            busy('cutting', function () {
              return op({ op: 'snip', start: +state.sel.a.toFixed(2), end: +state.sel.b.toFixed(2) })
                .then(function () { state.sel = null })
            })
          }
        }),
        el('button', {
          class: 'hvs-btn', text: 'Tighten pauses',
          onclick: function () { busy('tightening', function () { return op({ op: 'tighten' }) }) }
        }),
        el('button', {
          class: 'hvs-btn', text: '＋ title', title: 'render and insert a title card at the playhead selection (or 0)',
          onclick: function () {
            busy('rendering card', function () { return op({ op: 'card', kind: 'title', seconds: 1.2, at: state.sel ? state.sel.a : 0 }) })
          }
        }),
        el('button', {
          class: 'hvs-btn', text: '＋ end', title: 'render and insert an end card at the end',
          onclick: function () {
            busy('rendering card', function () { return op({ op: 'card', kind: 'end', seconds: 1.5 }) })
          }
        }),
        el('button', {
          class: 'hvs-btn', text: 'Undo',
          disabled: !v.ops.length,
          onclick: function () { busy('undoing', function () { return op({ op: 'undo' }) }) }
        }),
        el('span', { class: 'hvs-spacer' }),
        selNote,
        el('span', { class: 'hvs-spacer' }),
        el('button', {
          class: 'hvs-btn', text: 'Preview (fast)',
          onclick: function () { busy('rendering preview', function () { return doRender(true) }) }
        }),
        el('button', {
          class: 'hvs-btn primary', text: 'Render + gate',
          onclick: function () { busy('rendering', function () { return doRender(false) }) }
        })
      ])

      var tabs = el('div', { class: 'hvs-bar', style: { paddingTop: '0' } }, [
        el('button', {
          class: 'hvs-btn sm' + (playing === 'source' ? ' primary' : ''),
          text: 'source', onclick: function () { state.playing = 'source'; redraw() }
        }),
        latest ? el('button', {
          class: 'hvs-btn sm' + (playing === 'render' ? ' primary' : ''),
          text: 'latest ' + (latest.indexOf('preview') === 0 ? 'preview' : 'cut'),
          onclick: function () { state.playing = 'render'; redraw() }
        }) : el('span', { class: 'hvs-note', text: 'no render yet' })
      ])

      return el('div', { class: 'hvs-stage' }, [
        el('div', { class: 'hvs-head' }, [
          el('h1', { text: v.title }),
          el('div', { class: 'meta' }, [
            el('b', { text: fmt(v.duration) }), ' of ' + fmt(total),
            ' · ' + v.segments.length + ' segments · ' + v.op_count + ' ops' +
            (v.duration <= 90 ? '' : ' · OVER 90s CAP')
          ])
        ]),
        state.error ? el('div', { class: 'hvs-err', text: state.error }) : null,
        el('div', { class: 'hvs-player' }, [video]),
        tabs,
        wave,
        el('div', { class: 'hvs-ruler' }, [el('span', { text: '0:00' }), el('span', { text: fmt(total) })]),
        tl,
        bar
      ])
    }

    function renderInspector() {
      var v = state.view
      var checks = state.checks || []
      var rows = checks.map(function (c) {
        return el('div', { class: 'hvs-check' }, [
          el('span', { class: 'hvs-dot ' + (c.pass ? 'ok' : 'no') }),
          el('span', { class: 't', text: c.check }),
          el('span', { class: 'd', text: c.detail })
        ])
      })

      var a = state.analyze
      var facts = a ? el('div', null, [
        el('div', { class: 'hvs-check' }, [
          el('span', { class: 'hvs-dot na' }),
          el('span', { class: 't', text: 'pauses found' }),
          el('span', { class: 'd', text: String((a.suggested_tighten || []).length) })
        ]),
        el('div', { class: 'hvs-check' }, [
          el('span', { class: 'hvs-dot na' }),
          el('span', { class: 't', text: 'removable' }),
          el('span', { class: 'd', text: (a.would_remove_seconds || 0).toFixed(2) + 's' })
        ]),
        a.hook_is_static
          ? el('div', { class: 'hvs-note', style: { color: '#f59e0b', marginTop: '6px' },
                       text: 'first motion at ' + fmt(a.first_motion_at || 0) + ' — hook may be too still' })
          : null
      ]) : el('div', { class: 'hvs-note', text: 'analysing…' })

      var ops = (v && v.ops) ? v.ops.map(function (o, idx) { return { o: o, idx: idx } })
        .slice(-14).reverse().map(function (row) {
          var o = row.o
          var label = o.op
          if (o.op === 'snip') label += ' ' + fmt(o.start) + '–' + fmt(o.end)
          if (o.op === 'speed') label += ' ' + o.factor + '×'
          if (o.op === 'caption') label += ' "' + o.text + '"'
          if (o.op === 'card') label += ' ' + o.kind
          return el('div', { class: 'hvs-op' }, [
            el('b', { text: String(row.idx) }),
            el('span', { style: { flex: '1' }, text: label }),
            el('button', {
              class: 'hvs-x', title: 'delete just this op',
              text: '×',
              onclick: function () {
                busy('revising', function () { return op({ op: 'drop', index: row.idx }) })
              }
            })
          ])
        }) : []

      var renders = (v && v.renders) ? v.renders.slice(0, 6).map(function (r) {
        return el('div', { class: 'hvs-op', style: { cursor: 'pointer' },
          onclick: function () { state.playing = 'render'; redraw() } }, [
          el('b', { text: r.name.indexOf('preview') === 0 ? 'preview' : 'cut' }),
          r.name.replace(/^\w+-/, '').replace('.mp4', '') + ' · ' + (r.bytes / 1048576).toFixed(1) + 'MB'
        ])
      }) : [el('div', { class: 'hvs-note', text: 'none yet' })]

      return el('div', { class: 'hvs-insp' }, [
        el('div', { class: 'hvs-sec' }, [
          el('h3', { class: 'hvs-h', text: state.shippable == null ? 'Gate' : (state.shippable ? 'Gate · shippable' : 'Gate · blocked') }),
          rows.length ? el('div', null, rows) : el('div', { class: 'hvs-note', text: 'render to run the gate' })
        ]),
        el('div', { class: 'hvs-sec' }, [el('h3', { class: 'hvs-h', text: 'This clip' }), facts]),
        el('div', { class: 'hvs-sec' }, [
          el('h3', { class: 'hvs-h', text: 'Captions · edit in place' }),
          (v && v.captions && v.captions.length)
            ? el('div', null, v.captions.map(function (c) {
                var inp = el('input', {
                  class: 'hvs-inp', value: c.text, spellcheck: 'false',
                  style: { marginBottom: '4px' }
                })
                inp.addEventListener('change', function () {
                  busy('saving caption', function () {
                    return op({ op: 'edit', index: c.op_index, text: inp.value })
                  })
                })
                return el('div', { style: { marginBottom: '6px' } }, [
                  inp,
                  el('div', { class: 'hvs-note', text: fmt(c.start) + ' – ' + fmt(c.end) })
                ])
              }))
            : el('div', { class: 'hvs-note', text: 'no captions yet' })
        ]),
        el('div', { class: 'hvs-sec' }, [el('h3', { class: 'hvs-h', text: 'Ops (newest first) · × deletes one' }),
          ops.length ? el('div', null, ops) : el('div', { class: 'hvs-note', text: 'no edits yet' })]),
        el('div', { class: 'hvs-sec' }, [el('h3', { class: 'hvs-h', text: 'Renders' }), el('div', { class: 'hvs-renders' }, renders)])
      ])
    }

    // ---------------- data
    function loadProjects() {
      return api('/projects').then(function (r) {
        state.projects = r.projects || []
        redraw()
        return r
      })
    }

    function open(id) {
      state.id = id
      state.error = ''
      state.sel = null
      state.playing = null
      return api('/projects/' + id).then(function (v) {
        state.view = v
        deriveChecks(v)
        redraw()
        return api('/projects/' + id + '/analyze').then(function (a) {
          state.analyze = a
          redraw()
        }).catch(function () { state.analyze = null; redraw() })
      }).catch(function (e) { state.error = String(e.message || e); redraw() })
    }

    function deriveChecks(v) {
      // No render yet: show the facts we already have, not invented greens.
      state.checks = [
        { check: 'duration <= 90s', pass: v.duration <= 90, detail: v.duration.toFixed(2) + 's' },
        { check: 'source readable', pass: !!v.source_duration, detail: v.source_duration + 's' },
        { check: 'render exists', pass: (v.renders || []).length > 0, detail: (v.renders || []).length + ' file(s)' }
      ]
      state.shippable = null
    }

    function op(payload) {
      return api('/projects/' + state.id + '/ops', {
        method: 'POST', body: JSON.stringify(payload)
      }).then(function (r) {
        state.view = r
        state.error = ''
        deriveChecks(r)
        redraw()
        return api('/projects/' + state.id + '/analyze').then(function (a) { state.analyze = a; redraw() })
          .catch(function () {})
      }).then(function () { return loadProjects() })
        .catch(function (e) { state.error = String(e.message || e); redraw(); throw e })
    }

    function doRender(fast) {
      return api('/projects/' + state.id + '/render', {
        method: 'POST', body: JSON.stringify({ fast: !!fast })
      }).then(function (r) {
        state.checks = r.checks || []
        state.shippable = r.shippable
        state.error = ''
        state.notice = (fast ? 'preview' : 'cut') + ' ' + fmt(r.duration)
        state.playing = 'render'
        return api('/projects/' + state.id).then(function (v) { state.view = v; redraw() })
      }).catch(function (e) { state.error = String(e.message || e); redraw(); throw e })
    }

    function busy(label, fn) {
      state.busy = label
      state.error = ''
      redraw()
      Promise.resolve(fn()).catch(function () {}).then(function () {
        state.busy = ''
        redraw()
      })
    }

    // ---------------- boot
    loadVersion()
    loadProjects().then(function () {
      if (state.projects.length) return open(state.projects[0].id)
    }).catch(function (e) {
      state.error = 'cannot reach the studio backend: ' + String(e.message || e)
      redraw()
    })
    redraw()
  }

  // ------------------------------------------------------------- host glue
  var SDK = window.__HERMES_PLUGIN_SDK__
  if (SDK && SDK.React && window.__HERMES_PLUGINS__) {
    var React = SDK.React
    function Tab() {
      var ref = React.useRef(null)
      React.useEffect(function () {
        if (!ref.current) return
        ref.current.innerHTML = ''
        mount(ref.current)
      }, [])
      return React.createElement('div', {
        ref: ref, className: 'hvs-host',
        style: { position: 'absolute', inset: 0 }
      })
    }
    window.__HERMES_PLUGINS__.register(PLUGIN, Tab)
  } else {
    // standalone page (dev server): mount straight into the document
    var host = document.getElementById('root') || document.body
    mount(host)
  }
})()
