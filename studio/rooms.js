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
    ;['films', 'assets', 'generate'].forEach(function (r) {
      var view = $('room-' + r)
      if (view) view.hidden = r !== room
    })
    Array.prototype.forEach.call(document.querySelectorAll('.room'), function (b) {
      b.classList.toggle('on', b.getAttribute('data-room') === room)
    })
    if (room === 'assets') loadAssets()
    if (room === 'generate') loadComfy()
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
    }).catch(function (e) {
      body.innerHTML = ''
      body.appendChild(el('p', { class: 'hint', text: 'Status failed: ' + e.message }))
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
