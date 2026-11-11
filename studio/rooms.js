// Rooms beyond cutting: intake (screenshots, screen recordings) and generation (ComfyUI).
// Same rule as the rest of the studio — every number here came back from the API.
(function () {
  'use strict'

  var API = '/api'
  var state = { assets: [], comfy: null, busy: false }

  function $(id) { return document.getElementById(id) }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag)
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k]
      if (v == null || v === false) return
      if (k === 'class') n.className = v
      else if (k === 'text') n.textContent = v
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v)
      else n.setAttribute(k, v)
    })
    ;(kids || []).forEach(function (k) {
      if (k == null || k === false) return
      n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k)
    })
    return n
  }
  function status(msg) { var s = $('status-text'); if (s) s.textContent = msg }
  function api(path, opts) {
    return fetch(API + path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}))
      .then(function (r) {
        return r.text().then(function (txt) {
          var body = {}
          try { body = txt ? JSON.parse(txt) : {} } catch (e) { body = { detail: txt } }
          if (!r.ok) throw new Error(body.detail || ('HTTP ' + r.status))
          return body
        })
      })
  }
  function mb(bytes) {
    if (!bytes) return '—'
    return bytes < 1048576 ? Math.max(1, Math.round(bytes / 1024)) + ' KB'
                           : (bytes / 1048576).toFixed(1) + ' MB'
  }
  function secs(v) { return v == null ? '—' : (v < 10 ? v.toFixed(1) : Math.round(v)) + 's' }

  // ------------------------------------------------------------------- rooms
  function show(room) {
    ;['films', 'assets', 'generate', 'music', 'production'].forEach(function (r) {
      var view = $('room-' + r)
      if (view) view.hidden = r !== room
    })
    Array.prototype.forEach.call(document.querySelectorAll('.room'), function (b) {
      b.classList.toggle('on', b.getAttribute('data-room') === room)
    })
    if (room === 'assets') loadAssets()
    if (room === 'generate') loadComfy()
    if (room === 'music') loadMusic()
    if (room === 'production') { loadProduction(); loadCaptureDeps() }
    if (room === 'timeline' && window.__studioLoadTimeline) window.__studioLoadTimeline()
  }

  // ------------------------------------------------------------------ assets
  function loadAssets() {
    api('/assets').then(function (r) {
      state.assets = r.assets || []
      $('assets-sub').textContent = r.assets.length + ' assets · ' + r.library
      renderAssets()
    }).catch(function (e) { status('Cannot list assets: ' + e.message) })
  }

  function renderAssets() {
    var grid = $('asset-grid')
    grid.innerHTML = ''
    if (!state.assets.length) {
      grid.appendChild(el('p', { class: 'hint',
        text: 'Nothing ingested yet. Drop files in assets/intake, then press Scan intake.' }))
      return
    }
    state.assets.forEach(function (a) {
      var p = a.probe || {}
      var meta = a.kind === 'image'
        ? (p.width ? p.width + '×' + p.height : 'image')
        : [p.width && p.height ? p.width + '×' + p.height : null, secs(p.duration), p.fps ? p.fps + ' fps' : null,
           p.has_audio ? 'audio' : 'silent'].filter(Boolean).join(' · ')
      var actions = []
      if (a.kind === 'image') {
        actions.push(el('button', { class: 'btn', text: 'Make 4s clip', onclick: function () { makeStill(a.id) } }))
      }
      if (a.kind === 'video') {
        var from = el('input', { class: 'num', type: 'number', step: '0.1', value: '0', title: 'start (seconds)', 'aria-label': 'start seconds' })
        var to = el('input', { class: 'num', type: 'number', step: '0.1', value: '2', title: 'end (seconds)', 'aria-label': 'end seconds' })
        var dur = p.duration ? Math.min(2, Math.max(0.5, p.duration - 0.2)) : 2
        to.value = dur.toFixed(1)
        actions.push(el('span', { class: 'num-label', text: 'from' }), from,
                     el('span', { class: 'num-label', text: 'to' }), to,
                     el('span', { class: 'num-label', text: 's' }),
                     el('button', { class: 'btn', text: 'Trim',
          onclick: function () { trim(a.id, from.value, to.value) } }))
      }
      var thumb = a.thumb
        ? el('img', { class: 'asset-thumb', src: API + '/assets/' + a.id + '/thumb', alt: '',
            onclick: function () { preview(a) } })
        : el('div', { class: 'asset-thumb none', text: a.kind })
      if (a.kind === 'video') {
        thumb = el('div', { class: 'thumb-wrap', onclick: function () { preview(a) } }, [
          thumb, el('span', { class: 'play-badge', text: '▶' })
        ])
      }
      actions.push(el('button', { class: 'btn btn-ghost', text: 'Preview',
        onclick: function () { preview(a) } }))
      grid.appendChild(el('div', { class: 'asset-card' }, [
        thumb,
        el('div', { class: 'asset-meta' }, [
          el('b', { text: a.name || a.id }),
          el('span', { class: 'mono-dim', text: meta }),
          el('span', { class: 'mono-dim', text: mb(p.bytes) }),
          el('div', { class: 'asset-actions' }, actions)
        ])
      ]))
    })
  }

  function makeStill(id) {
    status('Making a moving clip from the still…')
    api('/assets/' + id + '/still', { method: 'POST', body: JSON.stringify({ seconds: 4, direction: 'in' }) })
      .then(function (r) { status('Still clip ready: ' + r.seconds + 's at ' + r.probe.width + '×' + r.probe.height); loadAssets() })
      .catch(function (e) { status('Still failed: ' + e.message) })
  }

  function trim(id, from, to) {
    status('Trimming…')
    api('/assets/' + id + '/trim', { method: 'POST', body: JSON.stringify({ start: Number(from), end: Number(to) }) })
      .then(function (r) { status('Trim ready: ' + r.seconds + 's'); loadAssets() })
      .catch(function (e) { status('Trim failed: ' + e.message) })
  }

  function scan() {
    status('Scanning the intake folder…')
    api('/assets/scan', { method: 'POST', body: JSON.stringify({ folders: [] }) }).then(function (r) {
      status('Added ' + (r.added || []).length + ', skipped ' + (r.skipped || []).length +
        (r.failed.length ? ', failed ' + r.failed.length : ''))
      loadAssets()
    }).catch(function (e) { status('Scan failed: ' + e.message) })
  }

  // ---------------------------------------------------------------- generate
  function loadComfy() {
    var body = $('gen-body')
    body.innerHTML = ''
    body.appendChild(el('p', { class: 'hint', text: 'Asking the GPU box what it can do…' }))
    api('/comfy/status').then(function (s) {
      state.comfy = s
      body.innerHTML = ''
      if (!s.ok) {
        body.appendChild(el('div', { class: 'gen-off' }, [
          el('b', { text: 'No ComfyUI answering' }),
          el('p', { class: 'mono-dim', text: s.host }),
          el('p', { class: 'hint', text: s.reason || '' }),
          el('p', { class: 'hint', text: 'Start it on the box that holds the weights, then press Check GPUs.' })
        ]))
      } else {
        var dev = (s.devices || [])[0] || {}
        body.appendChild(el('div', { class: 'gen-cards' }, (s.models || []).map(function (m) {
          return el('div', { class: 'gen-card ' + (m.ready ? 'ready' : '') }, [
            el('b', { text: m.label }),
            el('span', { class: 'mono-dim', text: m.ready ? 'installed: ' + m.installed.join(', ') : 'not installed here' })
          ])
        })))
        body.appendChild(el('div', { class: 'gen-foot mono-dim', text:
          (dev.name || 'unknown device') + ' · vram ' + (dev.vram_total ? Math.round(dev.vram_total / 1e9) + ' GB' : '?') +
          ' · queue ' + (s.queue ? (s.queue.running + ' running / ' + s.queue.pending + ' pending') : '?') }))
      }
      $('gen-sub').textContent = s.configured_host || s.host
      loadBeats()
    }).catch(function (e) {
      body.innerHTML = ''
      body.appendChild(el('p', { class: 'hint', text: 'Status failed: ' + e.message }))
    })
  }

  // ------------------------------------------------------------ generative beats
  //
  // One beat, one GPU job. The clip lands where the cut expects it
  // (renders/shots/<beat id>.mp4), so a film can mix typed shots and generated ones.
  function loadBeats() {
    var list = $('gen-beat-list')
    if (!list) return
    var fid = window.__studioFilm
    if (!fid) {
      list.innerHTML = ''
      list.appendChild(el('p', { class: 'hint', text: 'Pick a film in the rail first — then its beats appear here.' }))
      return
    }
    $('gen-beat-film').textContent = fid
    list.innerHTML = ''
    list.appendChild(el('p', { class: 'hint', text: 'Reading beats…' }))
    api('/films/' + encodeURIComponent(fid)).then(function (film) {
      list.innerHTML = ''
      ;(film.beats || []).forEach(function (b) {
        var busy = el('span', { class: 'mono-dim', text: '' })
        var btn = el('button', { class: 'btn small', text: 'Render with H3' })
        btn.addEventListener('click', function () {
          btn.disabled = true
          busy.textContent = 'queued… (this takes minutes, not seconds)'
          status('Rendering beat ' + b.id + ' with MiniMax H3')
          api('/comfy/beat', { method: 'POST', body: JSON.stringify({ film_id: fid, beat_id: b.id }) })
            .then(function (res) {
              busy.textContent = res.seconds + 's clip · ' + res.width + '×' + res.height +
                (res.comfy ? ' · ' + Math.round(res.comfy) + 's of GPU' : '')
              status('Beat ' + b.id + ' rendered')
            })
            .catch(function (e) { busy.textContent = 'refused: ' + e.message })
            .then(function () { btn.disabled = false })
        })
        list.appendChild(el('div', { class: 'gen-beat' }, [
          el('div', { class: 'gen-beat-name' }, [
            el('b', { text: b.id }),
            el('span', { class: 'mono-dim', text: ' ' + b.duration + 's · ' + (b.renderer || 'html') })
          ]),
          el('div', { class: 'gen-beat-prompt', text: b.shot || b.line || '' }),
          el('div', { class: 'gen-beat-actions' }, [btn, busy])
        ]))
      })
    }).catch(function (e) {
      list.innerHTML = ''
      list.appendChild(el('p', { class: 'hint', text: 'Could not read the film: ' + e.message }))
    })
  }

  function assemble() {
    var fid = window.__studioFilm
    if (!fid) { status('Pick a film first'); return }
    var btn = $('btn-gen-assemble')
    btn.disabled = true
    status('Assembling ' + fid + ' from its beat clips…')
    api('/films/' + encodeURIComponent(fid) + '/assemble', { method: 'POST', body: '{}' })
      .then(function (r) {
        status('Cut written: ' + r.output + ' (' + r.clips + ' clips, ' + r.seconds + 's)')
      })
      .catch(function (e) { status('Assemble refused: ' + e.message) })
      .then(function () { btn.disabled = false })
  }

  // ------------------------------------------------------------------- music
  //
  // Tracks are read from Music Studio's own store; the film keeps only a pointer
  // (track id, where to start, fades). Making music stays in the music room.
  // ------------------------------------------------------------------- build
  //
  // The chip answers "which code am I looking at" without a terminal. Amber when the installed
  // copy came from a dirty tree, click to copy the line into a bug report.
  function loadBuildChip() {
    var chip = $('build-chip')
    if (!chip) return
    api('/version').then(function (v) {
      chip.textContent = v.chip
      chip.classList.toggle('dirty', !!v.dirty)
      chip.title = (v.commit || 'no commit') + '\nbuilt ' + (v.built_at || 'unknown')
      chip.addEventListener('click', function () {
        var line = v.chip
        if (navigator.clipboard) navigator.clipboard.writeText(line)
        status('Copied: ' + line)
      })
    }).catch(function () { chip.textContent = 'unversioned' })
  }

  function loadMusic() {
    var list = $('music-list')
    var fid = window.__studioFilm
    if (!list) return
    list.innerHTML = ''
    list.appendChild(el('p', { class: 'hint', text: 'Reading Music Studio…' }))
    api('/music/library').then(function (lib) {
      list.innerHTML = ''
      if (!lib.tracks || !lib.tracks.length) {
        list.appendChild(el('div', { class: 'music-promo' }, [
          el('b', { text: 'No takes yet' }),
          el('span', { class: 'mono-dim', text: ' Music Studio has no rendered takes in ' + lib.store + '. Make one there first.' })
        ]))
        return
      }
      var current = null
      var render = function () {
        if (!fid) { list.appendChild(el('p', { class: 'hint', text: 'Pick a film in the rail, then choose a bed.' })); return }
        api('/films/' + encodeURIComponent(fid)).then(function (film) {
          current = film.music || null
          $('music-current').textContent = current
            ? 'bed: ' + current.title + ' · from ' + current.start + 's · fade ' + current.fade_in + 's / ' + current.fade_out + 's'
            : 'bed: none — the cut will use whatever renders/bed-*.wav exists, or be silent'
          lib.tracks.forEach(function (t) {
            var btn = el('button', { class: 'btn small', text: current && current.track_id === t.id ? 'In use' : 'Use as bed' })
            btn.disabled = !!(current && current.track_id === t.id)
            btn.addEventListener('click', function () {
              btn.disabled = true
              api('/films/' + encodeURIComponent(fid) + '/music', { method: 'POST',
                body: JSON.stringify({ track_id: t.id, start: 0, fade_in: 1.0, fade_out: 1.5 }) })
                .then(function () { window.__studioTrack = t.id; status('Bed set to “' + t.title + '”'); loadMusic() })
                .catch(function (e) { status('Could not set bed: ' + e.message); btn.disabled = false })
            })
            list.appendChild(el('div', { class: 'music-row' }, [
              el('div', { class: 'music-title' }, [el('b', { text: t.title }), el('span', { class: 'mono-dim', text: ' · ' + t.seconds + 's' })]),
              el('div', { class: 'music-meta mono-dim', text: [t.bpm ? t.bpm + ' bpm' : '', t.key || ''].filter(Boolean).join(' · ') }),
              el('div', { class: 'music-actions' }, [btn])
            ]))
          })
        }).catch(function (e) { $('music-current').textContent = 'film unreadable: ' + e.message })
      }
      render()
    }).catch(function (e) {
      list.innerHTML = ''
      list.appendChild(el('p', { class: 'hint', text: 'Library failed: ' + e.message }))
    })
  }

  function openMusicStudio() {
    api('/neighbour/music-studio')
      .then(function (r) { window.open(r.url, '_blank') })
      .catch(function (e) { status('Music Studio unreachable: ' + e.message) })
  }

  function clearMusic() {
    var fid = window.__studioFilm
    if (!fid) { status('Pick a film first'); return }
    api('/films/' + encodeURIComponent(fid) + '/music', { method: 'POST', body: JSON.stringify({ track_id: 'none' }) })
      .then(function () { status('Bed removed'); loadMusic() })
      .catch(function (e) { status('Could not clear: ' + e.message) })
  }

  // -------------------------------------------------------------- production
  //
  // The glue is a list, not a new plugin: each stage is an action that already exists, ordered
  // so the next one has something real to work with.
  var STAGES = [
    { key: 'script', title: '1 · Beats', what: 'What the film says, in timed beats.',
      run: function (fid) { return api('/script/' + encodeURIComponent(fid)) } },
    { key: 'typography', title: '2 · Typed shots', what: 'The words, on a fixed brand system.',
      run: function (fid) { return api('/script/' + encodeURIComponent(fid) + '/shots', { method: 'POST', body: JSON.stringify({ only_missing: true }) }) } },
    { key: 'capture', title: '3 · Real footage', what: 'The product actually being used.',
      run: function () { return api('/capture/run', { method: 'POST', body: JSON.stringify({ name: 'studio-walkthrough', walkthrough: true }) }) } },
    { key: 'broll', title: '4 · Generated b-roll', what: 'Cinematic footage from MiniMax H3.',
      run: function (fid) {
        return api('/films/' + encodeURIComponent(fid)).then(function (film) {
          var b = (film.beats || [])[0]; if (!b) throw new Error('no beats')
          return api('/comfy/beat', { method: 'POST', body: JSON.stringify({ film_id: fid, beat_id: b.id }) })
        })
      } },
    { key: 'stills', title: '5 · Stills', what: 'Generated frames, imported as assets.',
      run: function () { return api('/stills/scan', { method: 'POST', body: JSON.stringify({ as_clip: true }) }) } },
    { key: 'music', title: '6 · Music', what: 'A real track from Music Studio.',
      run: function (fid) { var t = window.__studioTrack; if (!t) throw new Error('pick a track in Music first'); return api('/films/' + encodeURIComponent(fid) + '/music', { method: 'POST', body: JSON.stringify({ track_id: t }) }) } },
    { key: 'drafts', title: '7 · Directions', what: 'Three cheap drafts, watched and judged.',
      run: function (fid) { return api('/films/' + encodeURIComponent(fid) + '/drafts', { method: 'POST', body: JSON.stringify({}) }) } },
    { key: 'cut', title: '8 · Cut', what: 'One cut, gated on hook, motion, audio, legibility.',
      run: function (fid) { return api('/films/' + encodeURIComponent(fid) + '/assemble', { method: 'POST', body: '{}' }) } }
  ]

  // Browser capture needs nothing; desktop capture needs a recorder, and the studio cannot
  // install system packages itself — it explains, offers, and re-checks afterwards.
  function loadCaptureDeps() {
    var body = $('cap-body'), state = $('cap-state')
    if (!body) return
    body.innerHTML = ''
    api('/capture/deps').then(function (d) {
      var desk = d.desktop || {}
      state.textContent = desk.ready ? 'desktop ready' : 'desktop unavailable'
      state.classList.toggle('warn', !desk.ready)
      body.appendChild(el('div', { class: 'cap-row' }, [
        el('div', {}, [el('b', { text: 'Browser capture' }),
          el('div', { class: 'mono-dim', text: d.browser.why })]),
        el('span', { class: 'cap-ok', text: 'ready' })
      ]))
      var actions = el('div', { class: 'cap-actions' })
      if (!desk.ready) {
        var install = el('button', { class: 'btn small', text: 'Install wf-recorder' })
        install.addEventListener('click', function () {
          install.disabled = true
          status('Asking the system for permission to install wf-recorder…')
          api('/capture/install', { method: 'POST', body: JSON.stringify({ prompt: true }) })
            .then(function (r) {
              if (r.ok) { status('wf-recorder installed'); loadCaptureDeps() }
              else { status(r.needs || r.hint || 'install needs a terminal'); install.disabled = false }
            })
            .catch(function (e) { status('Install refused: ' + e.message); install.disabled = false })
        })
        var copy = el('button', { class: 'btn small', text: 'Copy command' })
        copy.addEventListener('click', function () {
          if (navigator.clipboard) navigator.clipboard.writeText(desk.command)
          status('Copied: ' + desk.command)
        })
        actions.appendChild(install); actions.appendChild(copy)
      }
      body.appendChild(el('div', { class: 'cap-row' }, [
        el('div', {}, [el('b', { text: 'Desktop capture' }),
          el('div', { class: 'mono-dim', text: desk.ready ? ('recorder: ' + desk.recorder)
            : 'needs a recorder (' + (desk.package_manager || 'package manager') + ') — ' + desk.command })]),
        el('span', { class: desk.ready ? 'cap-ok' : 'cap-missing', text: desk.ready ? 'ready' : 'missing' })
      ]))
      if (actions.childNodes.length) body.appendChild(actions)
      if (desk.ready) {
        var open = el('button', { class: 'btn small', text: 'Record a take…' })
        // not `openRecorder` directly: the handler would receive the click event as the beat
        open.addEventListener('click', function () { openRecorder() })
        body.appendChild(el('div', { class: 'cap-actions' }, [open]))
      }
      body.appendChild(el('div', { class: 'cap-why' }, [
        el('b', { text: 'When desktop capture is the right tool' }),
        el('ul', {}, (d.when_useful || []).map(function (x) { return el('li', { text: x }) })),
        el('b', { text: 'When it is not' }),
        el('ul', {}, (d.when_not_needed || []).map(function (x) { return el('li', { text: x }) }))
      ]))
    }).catch(function (e) { body.appendChild(el('p', { class: 'hint', text: 'deps failed: ' + e.message })) })
  }

  // ------------------------------------------------------------------ review
  //
  // The player's own timestamp is the pointer: stop where it looks wrong, type what feels wrong,
  // and the model gets that frame's measurements plus the shot's source.
  function loadReviewModels() {
    var sel = $('review-model')
    if (!sel) return
    api('/models').then(function (r) {
      sel.innerHTML = ''
      ;(r.models || []).forEach(function (m) {
        var opt = el('option', { value: m.id, text: m.label + (m.status && m.status.up ? '' : ' — offline') })
        if (!(m.status && m.status.up)) opt.disabled = true
        sel.appendChild(opt)
      })
    }).catch(function () {})
  }

  function runReview() {
    var fid = window.__studioFilm
    var player = $('player')
    var note = ($('review-note') || {}).value || ''
    var model = ($('review-model') || {}).value || 'deepseek-lane'
    var out = $('review-out')
    if (!fid || !out) { status('Pick a film first'); return }
    var at = player ? (player.currentTime || 0) : 0
    out.innerHTML = ''
    out.appendChild(el('p', { class: 'hint', text: 'Asking ' + model + ' about ' + at.toFixed(1) + 's…' }))
    status('Reviewing ' + at.toFixed(1) + 's with ' + model)
    api('/review', { method: 'POST', body: JSON.stringify({ film: fid, time: at, note: note, model: model }) })
      .then(function (r) {
        out.innerHTML = ''
        if (!r.ok) { out.appendChild(el('p', { class: 'hint', text: 'model error: ' + r.error })); return }
        var rev = r.review
        if (!rev) {
          out.appendChild(el('pre', { class: 'review-raw', text: r.raw || '(no JSON returned)' }))
          return
        }
        out.appendChild(el('div', { class: 'review-diagnosis' }, [
          el('b', { text: rev.diagnosis || '' }),
          el('div', { class: 'mono-dim', text: (rev.evidence || []).join(' · ') })
        ]))
        ;(rev.changes || []).forEach(function (c) {
          out.appendChild(el('div', { class: 'review-change' }, [
            el('span', { class: 'review-pri', text: '#' + (c.priority || 1) }),
            el('div', {}, [el('b', { text: c.target || '' }), el('div', { text: c.change || '' }),
                           el('div', { class: 'mono-dim', text: c.why || '' })])
          ]))
        })
        if ((rev.keep || []).length) out.appendChild(el('div', { class: 'mono-dim', text: 'keep: ' + rev.keep.join(' · ') }))
        if (rev.risk) out.appendChild(el('div', { class: 'review-risk', text: 'risk: ' + rev.risk }))
        out.appendChild(el('div', { class: 'mono-dim', text: (r.image_sent ? 'frame sent' : 'frame not sent (model is text-only)')
          + ' · luma ' + r.measurements.luma + ' · motion ' + (r.measurements.motion_around || []).join('/') + '%' }))
      })
      .catch(function (e) { out.innerHTML = ''; out.appendChild(el('p', { class: 'hint', text: e.message })) })
  }

  // A recorder that is aimed and configured, not guessed: pick the window you are about to demo,
  // the frame rate, whether sound comes along, whether this take is for a beat — hit record,
  // drive the app, hit stop. The clip lands in Assets and can be cut straight into a beat.
  function openRecorder(beat) {
    var modal = $('rec-modal'), body = $('rec-body')
    if (!modal || !body) return
    body.innerHTML = ''
    body.appendChild(recorderPanel(beat))
    modal.hidden = false
    var intro = $('rec-intro')
    if (intro) intro.textContent = beat
      ? 'Recording for beat "' + beat + '". Hit record, drive the app, then use the take in that beat.'
      : 'Aim it at the window you are about to demo, pick a length, hit record, then drive the app.'
    var close = $('rec-close')
    if (close) close.onclick = function () { modal.hidden = true }
    modal.onclick = function (e) { if (e.target === modal) modal.hidden = true }
  }

  function recorderPanel(beatId) {
    var box = el('div', { class: 'cap-rec' })
    var row = el('div', { class: 'cap-rec-row' })
    var target = el('select', { class: 'review-model' })
    var quality = el('select', { class: 'review-model' })
    var fps = el('select', { class: 'review-model' })
    var secs = el('select', { class: 'review-model' })
    var smooth = el('label', { class: 'cap-audio' }, [el('input', { type: 'checkbox' }), el('span', { text: ' every frame' })])
    var audio = el('label', { class: 'cap-audio' }, [el('input', { type: 'checkbox' }), el('span', { text: ' sound' })])
    var mic = el('select', { class: 'review-model', hidden: true })
    var go = el('button', { class: 'btn small', text: 'Record' })
    var st = el('span', { class: 'mono-dim' })
    var timer = null, caps = null

    function opt(sel, value, text, selected) {
      var o = el('option', { value: value, text: text })
      if (selected) o.selected = true
      sel.appendChild(o)
    }

    api('/capture/record/capabilities').then(function (c) {
      caps = c
      var d = c.defaults || {}
      target.innerHTML = ''
      opt(target, '', 'Whole screen')
      api('/capture/record/targets').then(function (tg) {
        ;(tg.windows || []).forEach(function (w) {
          var o = el('option', { value: w.geometry, text: w.label + '  (' + w.size + ')' })
          o.dataset.kind = 'window'
          target.appendChild(o)
        })
        ;(tg.monitors || []).forEach(function (m) {
          var o = el('option', { value: m, text: 'Monitor ' + m })
          o.dataset.kind = 'monitor'
          target.appendChild(o)
        })
      }).catch(function () {})

      ;(c.qualities || ['standard']).forEach(function (q) { opt(quality, q, q, q === d.quality) })
      ;(c.framerate || [60]).forEach(function (f) { opt(fps, f, f + ' fps', f === d.fps) })
      ;[[15, '15 seconds'], [30, '30 seconds'], [60, '60 seconds'], [0, 'until I stop']].forEach(function (p) {
        opt(secs, p[0], p[1], p[0] === 0)
      })
      smooth.querySelector('input').checked = d.smooth !== false
      audio.querySelector('input').checked = !!d.audio
      ;(c.audio_sources || []).slice(0, 8).forEach(function (s) {
        opt(mic, s.name, s.name.replace(/^(alsa_output|alsa_input)\./, ''))
      })
      if (!(c.audio_sources || []).length) {
        mic.appendChild(el('option', { value: '', text: 'no sources found — is pactl installed?' }))
      }
      mic.hidden = !audio.querySelector('input').checked
      audio.querySelector('input').addEventListener('change', function () {
        mic.hidden = !audio.querySelector('input').checked
      })
    }).catch(function (e) { st.textContent = 'cannot read recorder capabilities: ' + e.message })

    function poll() {
      api('/capture/record/status').then(function (r) {
        if (r.recording) {
          st.textContent = 'recording ' + r.elapsed.toFixed(1) + 's · ' + (r.bytes / 1e6).toFixed(1) + ' MB'
            + (r.options ? ' · ' + r.options.fps + 'fps ' + r.options.quality : '')
            + (r.warning ? ' · ' + r.warning : '')
        } else if (r.state === 'stopped') {
          clearInterval(timer); timer = null
          go.textContent = 'Record'; go.classList.remove('danger')
          showResult(r.result || {}, r.film, r.beat)
        }
      }).catch(function () {})
    }

    function showResult(res, film, beat) {
      if (!res.path) return
      st.textContent = res.ok
        ? 'saved ' + res.seconds + 's · ' + (res.probe.width || '?') + '×' + (res.probe.height || '?')
          + (res.asset && res.asset.id ? ' · in Assets' : '')
        : 'failed: ' + ((res.probe || {}).reason || 'unknown')
      if (res.ok) box.appendChild(useInBeat(film || window.__studioFilm, beat))
      loadTakes()
    }

    function useInBeat(filmId, beatId) {
      var wrap = el('div', { class: 'cap-rec-beat' })
      var sel = el('select', { class: 'review-model' })
      var apply = el('button', { class: 'btn small', text: 'Use in beat' })
      if (!filmId) return el('div', { class: 'mono-dim', text: 'open a film to cut this into a beat' })
      api('/films/' + encodeURIComponent(filmId)).then(function (f) {
        (f.beats || []).forEach(function (b) {
          var o = el('option', { value: b.id, text: (b.title || b.id) + ' · ' + Math.round(b.duration || 0) + 's' })
          if (beatId && b.id === beatId) o.selected = true
          sel.appendChild(o)
        })
      }).catch(function () {})
      apply.addEventListener('click', function () {
        apply.disabled = true
        status('Cutting footage into ' + sel.value + '…')
        api('/films/' + encodeURIComponent(filmId) + '/beats/' + encodeURIComponent(sel.value) + '/footage',
            { method: 'POST', body: JSON.stringify({ capture: 'last' }) })
          .then(function (r) { status('Beat ' + r.beat + ' now plays real footage'); apply.disabled = false })
          .catch(function (e) { status('Could not use it: ' + e.message); apply.disabled = false })
      })
      wrap.appendChild(sel); wrap.appendChild(apply)
      return wrap
    }

    function loadTakes() {
      api('/capture/record/takes').then(function (r) {
        var list = r.takes || []
        if (!list.length) return
        var wrap = el('div', { class: 'cap-takes' })
        wrap.appendChild(el('div', { class: 'mono-dim', text: 'takes' }))
        list.slice(0, 5).forEach(function (t) {
          wrap.appendChild(el('div', { class: 'cap-take' }, [
            el('b', { text: t.name }),
            el('span', { class: 'mono-dim', text: (t.ok ? t.seconds + 's · ' + t.width + '×' + t.height
              : 'broken: ' + (t.reason || 'unknown')) + ' · ' + (t.bytes / 1e6).toFixed(1) + ' MB' })
          ]))
        })
        box.appendChild(wrap)
      }).catch(function () {})
    }

    go.addEventListener('click', function () {
      if (timer) {
        go.disabled = true
        api('/capture/record/stop', { method: 'POST', body: '{}' })
          .then(function (r) { status(r.ok ? 'Saved ' + r.seconds + 's' : 'Recording failed') })
          .catch(function (e) { status('Stop failed: ' + e.message) })
          .finally(function () { go.disabled = false })
        return
      }
      var chosen = target.options[target.selectedIndex]
      var kind = (chosen && chosen.dataset.kind) || ''
      var body = {
        name: 'desktop-' + Date.now(),
        seconds: Number(secs.value) || null,
        quality: quality.value, fps: Number(fps.value),
        smooth: smooth.querySelector('input').checked,
        audio: audio.querySelector('input').checked,
        audio_device: audio.querySelector('input').checked ? (mic.value || null) : null,
        film: window.__studioFilm || null
      }
      if (kind === 'monitor') body.output = target.value
      else if (target.value) body.geometry = target.value
      api('/capture/record/start', { method: 'POST', body: JSON.stringify(body) })
        .then(function (r) {
          go.textContent = 'Stop'; go.classList.add('danger')
          st.textContent = 'recording · ' + (r.cmd || '')
          timer = setInterval(poll, 1000); poll()
        })
        .catch(function (e) { status('Could not start: ' + e.message) })
    })

    // every control wears its own label: four unlabelled dropdowns are a guessing game
    function field(label, control) {
      return el('label', { class: 'cap-field' }, [el('span', { text: label }), control])
    }
    row.appendChild(field('Aim at', target))
    row.appendChild(field('Quality', quality))
    row.appendChild(field('Frame rate', fps))
    row.appendChild(field('Length', secs))
    row.appendChild(el('div', { class: 'cap-field cap-field-check' }, [el('span', { text: 'Options' }),
      el('div', { class: 'cap-checks' }, [smooth, audio])]))
    row.appendChild(go)
    box.appendChild(row)
    box.appendChild(mic)
    box.appendChild(st)
    if (beatId) box.dataset.beat = beatId
    loadTakes()
    return box
  }

  // Snapping the cut to the music: the difference between a cut and a slide show with a bed.
  function fitBeats() {
    var fid = window.__studioFilm
    if (!fid) { status('Pick a film first'); return }
    status('Snapping cuts to the beat…')
    api('/films/' + encodeURIComponent(fid) + '/fit-to-beats', { method: 'POST', body: JSON.stringify({}) })
      .then(function (r) {
        if (!r.applied) { status(r.reason || 'already on the beat'); return }
        status('On the beat: ' + r.before.percent + '% → ' + r.after.percent + '% · ' +
               r.moved.length + ' cut(s) moved · re-render needed')
        var el = document.getElementById('beat-note')
        if (el) el.textContent = 'on beat ' + r.after.percent + '% (' + r.after.on + '/' + r.after.of +
          ') at ' + r.bpm + ' bpm'
      })
      .catch(function (e) { status('Could not fit: ' + e.message) })
  }

  function loadBeatNote() {
    var fid = window.__studioFilm
    var box = document.getElementById('beat-note')
    if (!box || !fid) return
    api('/films/' + encodeURIComponent(fid) + '/beat-grid')
      .then(function (d) { box.textContent = 'on beat ' + d.percent + '% (' + d.on + '/' + d.of + ') at ' + d.bpm + ' bpm' })
      .catch(function () { box.textContent = 'no music attached' })
  }

  function loadProduction() {
    var body = $('prod-body'); if (!body) return
    body.innerHTML = ''
    var fid = window.__studioFilm
    body.appendChild(el('p', { class: 'hint', text: fid ? 'Film: ' + fid : 'Pick a film in the rail first.' }))
    STAGES.forEach(function (s) {
      var out = el('span', { class: 'mono-dim', text: '' })
      var btn = el('button', { class: 'btn small', text: 'Run' })
      btn.disabled = !fid
      btn.addEventListener('click', function () {
        btn.disabled = true; out.textContent = 'running…'
        status(s.title + ' — running')
        Promise.resolve().then(function () { return s.run(fid) })
          .then(function (r) { out.textContent = 'done: ' + JSON.stringify(r).slice(0, 110); status(s.title + ' — done') })
          .catch(function (e) { out.textContent = 'refused: ' + e.message; status(s.title + ' — ' + e.message) })
          .then(function () { btn.disabled = false })
      })
      body.appendChild(el('div', { class: 'prod-row' }, [
        el('div', { class: 'prod-name' }, [el('b', { text: s.title }), el('div', { class: 'mono-dim', text: s.what })]),
        out, btn
      ]))
    })
  }

  function upload(files) {
    if (!files || !files.length) return
    var list = Array.prototype.slice.call(files)
    status('Ingesting ' + list.length + ' file' + (list.length > 1 ? 's' : '') + '…')
    var done = 0
    list.forEach(function (file) {
      api('/assets/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file })
        .then(function () { done++; })
        .catch(function (e) { status('Upload failed: ' + e.message) })
        .then(function () {
          if (done === list.length) { status('Ingested ' + done + ' file' + (done > 1 ? 's' : '')); loadAssets() }
        })
    })
  }

  function preview(a) {
    var back = el('div', { class: 'modal-back', onclick: function (e) { if (e.target === back) document.body.removeChild(back) } })
    var box = el('div', { class: 'modal' })
    if (a.kind === 'image') {
      box.appendChild(el('img', { class: 'modal-media', src: API + '/assets/' + a.id + '/master' }))
    } else {
      box.appendChild(el('video', { class: 'modal-media', src: API + '/assets/' + a.id + '/proxy',
        controls: 'controls', autoplay: 'autoplay', playsinline: 'playsinline' }))
    }
    box.appendChild(el('div', { class: 'modal-foot mono-dim', text:
      a.name + ' · ' + (a.probe && a.probe.width ? a.probe.width + '×' + a.probe.height : a.kind) }))
    back.appendChild(box)
    document.body.appendChild(back)
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('.room'), function (b) {
      b.addEventListener('click', function () { show(b.getAttribute('data-room')) })
    })
    var s = $('btn-scan'); if (s) s.addEventListener('click', scan)
    var g = $('btn-gen-refresh'); if (g) g.addEventListener('click', loadComfy)
    var asm = $('btn-gen-assemble'); if (asm) asm.addEventListener('click', assemble)
    var mr = $('btn-music-refresh'); if (mr) mr.addEventListener('click', loadMusic)
    var mc = $('btn-music-clear'); if (mc) mc.addEventListener('click', clearMusic)
    var om = $('btn-open-music'); if (om) om.addEventListener('click', openMusicStudio)
    var pr = $('btn-prod-refresh'); if (pr) pr.addEventListener('click', loadProduction)
    loadBuildChip()
    loadReviewModels()
    var rv = $('btn-review'); if (rv) rv.addEventListener('click', runReview)
    var fb = $('btn-fit-beats'); if (fb) fb.addEventListener('click', fitBeats)
    loadBeatNote()
    var pl = $('player')
    if (pl) pl.addEventListener('timeupdate', function () {
      var at = $('review-at'); if (at) at.textContent = 'at ' + (pl.currentTime || 0).toFixed(1) + 's'
    })
    var pick = $('file-input'); if (pick) pick.addEventListener('change', function () { upload(pick.files); pick.value = '' })
    var add = $('btn-add'); if (add) add.addEventListener('click', function () { if (pick) pick.click() })
    var grid = $('asset-grid')
    if (grid) {
      ;['dragenter', 'dragover'].forEach(function (ev) {
        grid.addEventListener(ev, function (e) { e.preventDefault(); grid.classList.add('dropping') })
      })
      ;['dragleave', 'drop'].forEach(function (ev) {
        grid.addEventListener(ev, function (e) { e.preventDefault(); grid.classList.remove('dropping') })
      })
      grid.addEventListener('drop', function (e) { upload(e.dataTransfer.files) })
    }
  })
})()
