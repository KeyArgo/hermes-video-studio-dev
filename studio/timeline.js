// The timeline room: tracks with clips you can drag, trim and split. State lives on the server;
// this file only sends operations and draws what comes back.
(function () {
  'use strict'
  var API = '/api'
  var FILM = 'demo'
  function filmId() { return window.__studioFilm || FILM }
  var PPS = 64                    // pixels per second, fixed for now
  var TRACKS = ['V1', 'V2', 'A1', 'A2']
  var state = { timeline: null, assets: [], selected: null, busy: false }

  function $(id) { return document.getElementById(id) }
  function el(tag, attrs, kids) {
    var n = document.createElement(tag)
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k]
      if (v == null || v === false) return
      if (k === 'class') n.className = v
      else if (k === 'text') n.textContent = v
      else if (k === 'style') n.setAttribute('style', v)
      else if (k.slice(0, 2) === 'on') n.addEventListener(k.slice(2), v)
      else n.setAttribute(k, v)
    })
    ;(kids || []).forEach(function (k) {
      if (k == null || k === false) return
      n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k)
    })
    return n
  }
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
  function note(msg) { var s = $('tl-status'); if (s) s.textContent = msg }

  function load() {
    api('/timeline/' + filmId()).then(function (t) { state.timeline = t; draw() })
      .catch(function (e) { note('Cannot read timeline: ' + e.message) })
  }

  function draw() {
    var t = state.timeline
    if (!t) return
    var width = Math.max(600, Math.ceil((t.total || 10) * PPS) + 200)
    var ruler = $('tl-ruler')
    ruler.innerHTML = ''
    ruler.style.width = width + 'px'
    for (var s = 0; s <= (t.total || 10) + 2; s++) {
      ruler.appendChild(el('div', { class: 'tl-tick' + (s % 5 === 0 ? ' big' : ''),
        style: 'left:' + (s * PPS) + 'px', text: s % 5 === 0 ? s + 's' : '' }))
    }

    var tracks = $('tl-tracks')
    tracks.innerHTML = ''
    TRACKS.forEach(function (name) {
      var lane = el('div', { class: 'tl-lane', 'data-track': name }, [
        el('span', { class: 'tl-lane-name', text: name })
      ])
      var inner = el('div', { class: 'tl-lane-clips', style: 'width:' + width + 'px' })
      ;(t.tracks[name] || []).forEach(function (clip) {
        var node = el('div', {
          class: 'tl-clip' + (state.selected === clip.id ? ' sel' : '') + (name === 'A1' || name === 'A2' ? ' audio' : ''),
          style: 'left:' + (clip.start * PPS) + 'px;width:' + Math.max(12, clip.duration * PPS) + 'px'
        }, [
          el('span', { class: 'tl-clip-label', text: clip.id }),
          el('span', { class: 'tl-clip-dur', text: clip.duration.toFixed(1) + 's' }),
          el('div', { class: 'tl-handle left' }),
          el('div', { class: 'tl-handle right' })
        ])
        wireDrag(node, clip, name, inner)
        inner.appendChild(node)
      })
      lane.appendChild(inner)
      tracks.appendChild(lane)
    })
    note((t.total || 0).toFixed(1) + 's · ' + TRACKS.map(function (n) {
      return n + ' ' + (t.tracks[n] || []).length
    }).join(' · '))
  }

  function wireDrag(node, clip, track, lane) {
    var dragging = null
    node.addEventListener('mousedown', function (e) {
      e.preventDefault()
      state.selected = clip.id
      var mode = 'move'
      if (e.target.classList.contains('tl-handle')) mode = e.target.classList.contains('left') ? 'trim-in' : 'trim-out'
      dragging = { mode: mode, x0: e.clientX, start: clip.start, duration: clip.duration, inPoint: clip.in || 0 }
      document.body.style.cursor = mode === 'move' ? 'grabbing' : 'ew-resize'
    })
    window.addEventListener('mousemove', function (e) {
      if (!dragging) return
      var delta = (e.clientX - dragging.x0) / PPS
      if (dragging.mode === 'move') {
        node.style.left = Math.max(0, (dragging.start + delta) * PPS) + 'px'
      } else if (dragging.mode === 'trim-in') {
        var dIn = Math.max(-dragging.inPoint, Math.min(delta, dragging.duration - 0.2))
        node.style.left = (dragging.start + dIn) * PPS + 'px'
        node.style.width = Math.max(12, (dragging.duration - dIn) * PPS) + 'px'
      } else {
        node.style.width = Math.max(12, (dragging.duration + delta) * PPS) + 'px'
      }
    })
    window.addEventListener('mouseup', function (e) {
      if (!dragging) return
      var delta = (e.clientX - dragging.x0) / PPS
      var op = null
      if (dragging.mode === 'move' && Math.abs(delta) > 0.02) {
        op = { op: 'move', clip_id: clip.id, start: Math.max(0, round(dragging.start + delta)) }
      } else if (dragging.mode === 'trim-in' && Math.abs(delta) > 0.02) {
        var dIn = Math.max(-dragging.inPoint, Math.min(delta, dragging.duration - 0.2))
        op = { op: 'trim', clip_id: clip.id, in: round(dragging.inPoint + dIn),
               duration: round(dragging.duration - dIn) }
        op.start = round(dragging.start + dIn)
      } else if (dragging.mode === 'trim-out' && Math.abs(delta) > 0.02) {
        op = { op: 'trim', clip_id: clip.id, duration: round(Math.max(0.2, dragging.duration + delta)) }
      }
      dragging = null
      document.body.style.cursor = ''
      if (!op) { draw(); return }
      if (op.start != null) {
        // trim-from-the-left moves the clip as well: send both, in order
        var ops = [{ op: 'trim', clip_id: clip.id, in: op.in, duration: op.duration },
                   { op: 'move', clip_id: clip.id, start: op.start }]
        send(ops)
      } else send([op])
    })
  }

  function round(v) { return Math.round(v * 100) / 100 }

  function send(ops) {
    api('/timeline/' + filmId() + '/ops', { method: 'POST', body: JSON.stringify({ ops: ops }) })
      .then(function (t) { state.timeline = t; draw() })
      .catch(function (e) { note('Refused: ' + e.message); load() })
  }

  function fromFilm() {
    note('Seeding the timeline from ' + filmId() + '…')
    api('/timeline/' + filmId() + '/from-film', { method: 'POST' }).then(function (r) {
      state.timeline = r.state
      draw()
      note('placed ' + r.placed.length + ' · missing ' + r.missing.length +
        (r.missing.length ? ' (' + r.missing.join(', ') + ')' : ''))
    }).catch(function (e) { note('Cannot seed: ' + e.message) })
  }

  function undo() {
    api('/timeline/' + filmId() + '/undo', { method: 'POST' })
      .then(function (t) { state.timeline = t; draw(); note('undone') })
      .catch(function (e) { note('Nothing to undo: ' + e.message) })
  }

  function addClip() {
    api('/assets').then(function (r) {
      state.assets = r.assets || []
      var back = el('div', { class: 'modal-back' })
      var box = el('div', { class: 'modal' }, [el('b', { text: 'Add a clip' })])
      state.assets.forEach(function (a) {
        var dur = (a.probe && a.probe.duration) || 4
        var path = a.clip || (a.probe && a.kind) || ''
        box.appendChild(el('button', { class: 'pick', onclick: function () {
          document.body.removeChild(back)
          send([{ op: 'place', asset: assetPath(a), track: 'V1', start: state.timeline ? state.timeline.total : 0,
                  duration: Math.min(4, dur) }])
        } }, [
          el('b', { text: a.name || a.id }),
          el('span', { class: 'mono-dim', text: (a.kind || '') + ' · ' + (dur ? dur.toFixed(1) + 's' : 'still') })
        ]))
      })
      back.appendChild(box)
      back.addEventListener('click', function (e) { if (e.target === back) document.body.removeChild(back) })
      document.body.appendChild(back)
    })
  }

  function assetPath(a) {
    // the library keeps the master next to its clips; the edit wants a file, not an id
    return a.clip || a.master || a.id
  }

  function renderTimeline() {
    note('Rendering through MLT… this waits, it does not poll')
    $('btn-tl-render').disabled = true
    api('/timeline/' + filmId() + '/render', { method: 'POST', body: JSON.stringify({ quality: 'draft' }) })
      .then(function (r) { note('Rendered ' + r.seconds.toFixed(2) + 's · ' + Math.round(r.bytes / 1048576 * 10) / 10 + ' MB') })
      .catch(function (e) { note('Render failed: ' + e.message) })
      .then(function () { $('btn-tl-render').disabled = false })
  }

  window.__studioLoadTimeline = load
  document.addEventListener('DOMContentLoaded', function () {
    var a = $('btn-tl-add'); if (a) a.addEventListener('click', addClip)
    var u = $('btn-tl-undo'); if (u) u.addEventListener('click', undo)
    var r = $('btn-tl-render'); if (r) r.addEventListener('click', renderTimeline)
    var f = $('btn-tl-from-film'); if (f) f.addEventListener('click', fromFilm)
  })
})()
