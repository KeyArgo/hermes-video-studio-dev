// Video Studio — the studio app served by the loopback host.
// Every number on screen came back from /api; nothing here guesses.
(function () {
  'use strict'

  var API = '/api'
  var state = { films: [], id: null, film: null, gate: null, job: null, shot: null, busy: false }

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
  function fmt(t) {
    if (t == null || isNaN(t)) return '—'
    var m = Math.floor(t / 60), s = t - m * 60
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1)
  }
  function status(msg) { $('status-text').textContent = msg }

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

  // ---------------------------------------------------------------- films
  function loadFilms() {
    status('Loading films…')
    api('/films').then(function (r) {
      state.films = r.films || []
      $('rail-foot').textContent = r.workspace || ''
      renderFilms()
      if (state.films.length && !state.id) openFilm(state.films[0].id)
      else status('Ready')
    }).catch(function (e) { status('Cannot list films: ' + e.message) })
  }

  function renderFilms() {
    var box = $('films')
    box.innerHTML = ''
    if (!state.films.length) {
      box.appendChild(el('div', { class: 'hint', style: 'padding:12px 14px',
        text: 'No films in this workspace yet.' }))
      return
    }
    state.films.forEach(function (f) {
      box.appendChild(el('button', {
        class: 'film' + (f.id === state.id ? ' on' : ''),
        onclick: function () { openFilm(f.id) }
      }, [
        el('b', { text: f.title }),
        el('span', { text: fmt(f.total) + ' · ' + f.beats + ' shot' + (f.beats === 1 ? '' : 's') + ' · '
          + f.renders.length + ' render' + (f.renders.length === 1 ? '' : 's') })
      ]))
    })
  }

  function openFilm(id) {
    window.__studioFilm = id
    state.id = id
    state.gate = null
    state.shot = null
    renderFilms()
    renderInspector()
    api('/films/' + encodeURIComponent(id)).then(function (r) {
      state.film = r
      renderStage()
    }).catch(function (e) { status('Cannot open ' + id + ': ' + e.message) })
  }

  // ---------------------------------------------------------------- stage
  function renderStage() {
    var f = state.film
    if (!f) return
    $('film-title').textContent = f.title
    $('film-sub').textContent = fmt(f.total) + ' · ' + f.beats.length + ' shots · ' +
      (f.renders.length ? f.renders[0].name : 'not rendered')

    var player = $('player')
    var empty = $('player-empty')
    if (f.renders.length) {
      player.src = API + '/films/' + encodeURIComponent(f.id) + '/video?t=' + (f.renders[0].mtime || 0)
      player.classList.add('on')
      empty.style.display = 'none'
    } else {
      player.removeAttribute('src')
      player.classList.remove('on')
      empty.style.display = 'block'
    }

    var beats = $('beats')
    beats.innerHTML = ''
    f.beats.forEach(function (b, i) {
      beats.appendChild(el('div', { class: 'beat' + (b.exists ? '' : ' missing') }, [
        el('div', { class: 'beat-top' }, [
          el('span', { class: 'beat-n', text: String(i + 1).padStart(2, '0') + ' / ' + String(f.beats.length).padStart(2, '0') }),
          el('span', { class: 'beat-state' + (b.shot_render ? ' done' : ''), text: b.shot_render ? 'shot ✓' : 'shot —' })
        ]),
        el('div', { class: 'beat-d', text: b.duration.toFixed(1) + 's' }),
        el('div', { class: 'beat-n', text: b.id.replace(/^shot-/, '') }),
        el('div', { class: 'beat-btns' }, [
          el('button', { class: 'btn', text: 'Re-render', onclick: function () { renderShot(b.id) } }),
          el('button', { class: 'btn', text: 'Edit', onclick: function () { editShot(b.id) } })
        ])
      ]))
    })
    status('Ready · ' + f.beats.length + ' shots')
  }

  // ---------------------------------------------------------------- jobs
  function pollJob(jid) {
    state.job = { id: jid, state: 'running' }
    renderInspector()
    var tick = function () {
      api('/jobs/' + jid).then(function (j) {
        state.job = j
        renderInspector()
        if (j.state === 'running') { setTimeout(tick, 2000); return }
        if (j.state === 'done') {
          status((j.kind === 'shot' ? 'Shot rendered' : 'Film rendered') + ' in ' + j.seconds + 's')
          openFilm(state.id)
        } else {
          status('Render failed (rc ' + j.rc + ')')
        }
      }).catch(function (e) { status('Job lost: ' + e.message) })
    }
    setTimeout(tick, 1200)
  }

  function renderFilm() {
    status('Starting render…')
    api('/films/' + encodeURIComponent(state.id) + '/render', { method: 'POST' })
      .then(function (r) { pollJob(r.job) })
      .catch(function (e) { status('Render refused: ' + e.message) })
  }

  function renderShot(bid) {
    status('Rendering ' + bid + '…')
    api('/films/' + encodeURIComponent(state.id) + '/beats/' + encodeURIComponent(bid) + '/render', { method: 'POST' })
      .then(function (r) { pollJob(r.job) })
      .catch(function (e) { status('Render refused: ' + e.message) })
  }

  function runGate() {
    status('Running gate…')
    api('/films/' + encodeURIComponent(state.id) + '/gate', { method: 'POST' })
      .then(function (r) { state.gate = r; renderInspector(); status('Gate: ' + (r.shippable ? 'shippable' : 'failed')) })
      .catch(function (e) { state.gate = null; renderInspector(); status('Gate refused: ' + e.message) })
  }

  // ---------------------------------------------------------------- shots
  function editShot(bid) {
    state.shot = { id: bid, source: '…' }
    renderInspector()
    api('/films/' + encodeURIComponent(state.id) + '/beats/' + encodeURIComponent(bid) + '/source')
      .then(function (r) { state.shot = { id: bid, source: r.source }; renderInspector() })
      .catch(function (e) { state.shot = null; renderInspector(); status('Cannot read shot: ' + e.message) })
  }

  function saveShot() {
    var node = $('shot-src')
    if (!node || !state.shot) return
    api('/films/' + encodeURIComponent(state.id) + '/beats/' + encodeURIComponent(state.shot.id) + '/source',
      { method: 'PUT', body: JSON.stringify({ source: node.value }) })
      .then(function () { status('Shot saved — re-render it to see the change'); state.shot = null; openFilm(state.id) })
      .catch(function (e) { status('Save refused: ' + e.message) })
  }

  // ------------------------------------------------------------ inspector
  function renderInspector() {
    var g = $('gate-body')
    g.innerHTML = ''
    if (state.gate) {
      g.appendChild(el('div', { class: 'verdict ' + (state.gate.shippable ? 'pass' : 'fail'),
        text: state.gate.shippable ? 'shippable' : 'failed: ' + (state.gate.failed || []).join(', ') }))
      ;(state.gate.checks || []).forEach(function (c) {
        g.appendChild(el('div', { class: 'check ' + (c.pass ? 'pass' : 'fail') }, [
          el('span', { class: 'mark', text: c.pass ? '✓' : '✗' }),
          el('span', null, [el('span', { text: c.check }), el('small', { text: c.detail })])
        ]))
      })
      g.appendChild(el('div', { class: 'job-line', text: state.gate.video }))
    } else {
      g.appendChild(el('p', { class: 'hint', text: 'Hook, motion and legibility on the latest render. A film only ships when all three pass.' }))
    }

    var r = $('render-body')
    r.innerHTML = ''
    if (state.job) {
      r.appendChild(el('div', { class: 'job-line ' + state.job.state,
        text: state.job.state + (state.job.seconds ? ' · ' + state.job.seconds + 's' : '') }))
      if (state.job.tail) r.appendChild(el('div', { class: 'job-tail', text: String(state.job.tail).slice(-600) }))
    } else {
      r.appendChild(el('p', { class: 'hint', text: 'Nothing rendering.' }))
    }

    $('shot-name').textContent = state.shot ? state.shot.id : ''
    var s = $('shot-body')
    s.innerHTML = ''
    if (state.shot) {
      var ta = el('textarea', { id: 'shot-src', spellcheck: 'false' })
      ta.value = state.shot.source
      s.appendChild(ta)
      s.appendChild(el('div', { class: 'shot-actions' }, [
        el('button', { class: 'btn btn-primary', text: 'Save shot', onclick: saveShot }),
        el('button', { class: 'btn', text: 'Close', onclick: function () { state.shot = null; renderInspector() } })
      ]))
    } else {
      s.appendChild(el('p', { class: 'hint', text: 'Pick Edit on a shot to inspect its composition.' }))
    }
  }

  // ---------------------------------------------------------- version chip
  // One source: the server's own stamp (/api/version -> BUILD_INFO.json -> live git
  // -> plugin.yaml). A number compiled into this file would drift from the code
  // answering the requests.
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

  function showVersion() {
    var chip = $('app-version')
    if (!chip) return
    api('/version').then(function (data) {
      var build = (data && data.build) || {}
      // a missing build stamp is a state, not a version called "vunknown"
      var parts = [build.version ? 'v' + build.version : 'build unknown']
      if (build.commit_date) parts.push(String(build.commit_date).slice(0, 10))
      if (build.commit_short || build.commit) parts.push((build.commit_short || build.commit).slice(0, 8))
      chip.textContent = parts.join(' · ') + (build.dirty ? ' · modified' : '')
      chip.classList.toggle('dirty', !!build.dirty)
      chip.title = [
        'Video Studio ' + (build.version || 'build unknown'),
        build.commit ? 'commit ' + build.commit : 'no commit (installed copy)',
        build.commit_date ? 'committed ' + build.commit_date : '',
        build.built_at ? 'installed ' + build.built_at : '',
        build.channel ? 'channel ' + build.channel : '',
        build.source ? 'read from ' + build.source : '',
        'click to copy this line'
      ].filter(Boolean).join('\n')
      chip.onclick = async function () {
        var line = 'Video Studio ' + chip.textContent.trim()
        // Say "copied" only when something was copied: a badge that lies is worse than no badge.
        var copied = await copyText(line)
        chip.dataset.copied = copied ? '1' : 'failed'
        setTimeout(function () { delete chip.dataset.copied }, 1400)
      }
    }).catch(function () { /* no guess beats a wrong number */ })
  }

  $('btn-render').addEventListener('click', renderFilm)
  $('btn-gate').addEventListener('click', runGate)
  showVersion()
  loadFilms()
})()
