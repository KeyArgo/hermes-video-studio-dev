// The script room: an idea in, editable beats out. Every number shown came from the API.
(function () {
  'use strict'
  var API = '/api'
  var state = { treatment: null, filmId: null, busy: false }

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
  function note(msg) { var s = $('script-status'); if (s) s.textContent = msg }

  function draft() {
    var idea = $('idea').value.trim()
    if (!idea) { note('Write the idea first.'); return }
    note('Drafting with the lane… (10–30 s)')
    $('btn-draft').disabled = true
    api('/script/draft', { method: 'POST', body: JSON.stringify({
      idea: idea,
      seconds: Number($('target-secs').value) || 30,
      shots: Number($('target-shots').value) || 5
    }) }).then(function (t) {
      state.treatment = t
      $('btn-save-script').disabled = false
      var total = t.beats.reduce(function (a, b) { return a + Number(b.seconds || 0) }, 0)
      note(t.beats.length + ' beats · ' + total.toFixed(1) + 's of ' + t.total + 's')
      render();
    }).catch(function (e) {
      note('Refused: ' + e.message)
      state.treatment = null
      $('btn-save-script').disabled = true
    }).then(function () { $('btn-draft').disabled = false })
  }

  function render() {
    var box = $('beats-edit')
    box.innerHTML = ''
    if (!state.treatment) {
      box.appendChild(el('p', { class: 'hint', text: 'Nothing drafted yet.' }))
      return
    }
    var t = state.treatment
    box.appendChild(el('div', { class: 'beats-title' }, [
      el('b', { text: t.title }),
      el('span', { class: 'mono-dim', text: t.logline || '' })
    ]))
    t.beats.forEach(function (b, i) {
      var line = el('textarea', { class: 'beat-line', rows: '2' })
      line.value = b.line
      line.addEventListener('input', function () { b.line = line.value })
      var secs = el('input', { class: 'num', type: 'number', step: '0.5', min: '2', value: String(b.seconds) })
      secs.addEventListener('input', function () {
        b.seconds = Number(secs.value) || 0
        note(totalNow() + 's total')
      })
      var prompt = el('input', { class: 'beat-prompt', type: 'text', placeholder: 'shot prompt (optional)' })
      prompt.value = b.prompt || ''
      prompt.addEventListener('input', function () { b.prompt = prompt.value })
      box.appendChild(el('div', { class: 'beat-row' }, [
        el('div', { class: 'beat-head' }, [
          el('span', { class: 'beat-n', text: String(i + 1).padStart(2, '0') }),
          el('b', { text: b.label || b.id }),
          secs, el('span', { class: 'mono-dim', text: 's' })
        ]),
        line, prompt
      ]))
    })
    box.appendChild(el('div', { class: 'beats-foot mono-dim', text:
      'Durations must stay within 6 % of ' + t.total + 's — Save validates before writing.' }))
  }

  function totalNow() {
    if (!state.treatment) return '0.0'
    return state.treatment.beats.reduce(function (a, b) { return a + Number(b.seconds || 0) }, 0).toFixed(1)
  }

  function save() {
    if (!state.treatment) return
    var t = state.treatment
    note('Saving…')
    api('/script/save', { method: 'POST', body: JSON.stringify({
      title: t.title, logline: t.logline, accent: t.accent,
      beats: t.beats.map(function (b) {
        return { id: b.id, label: b.label, line: b.line, seconds: Number(b.seconds),
                 visual: b.visual, prompt: b.prompt, renderer: b.renderer }
      }),
      seconds: Number(t.total)
    }) }).then(function (r) {
      note('Saved ' + r.id + ' · ' + r.beats.length + ' beats · ' + r.total + 's')
      state.filmId = r.id
      var b = $('btn-build-script'); if (b) b.disabled = false
      if (window.__studioReloadFilms) window.__studioReloadFilms()
    }).catch(function (e) { note('Save refused: ' + e.message) })
  }

  function build() {
    if (!state.filmId) { note('Save the film first.'); return }
    note('Authoring shots with the lane, then rendering… this takes minutes, not seconds.')
    $('btn-build-script').disabled = true
    api('/script/' + state.filmId + '/build', { method: 'POST', body: JSON.stringify({ only_missing: true }) })
      .then(function (r) {
        var w = r.shots.written.length, f = r.shots.failed.length
        note('wrote ' + w + ' shots' + (f ? ', ' + f + ' refused' : '') + ' · rendering ' + r.job)
        if (r.shots.failed.length) {
          r.shots.failed.forEach(function (x) { note('refused ' + x.id + ': ' + x.error) })
        }
        pollBuild(r.job)
      })
      .catch(function (e) { note('Build refused: ' + e.message); $('btn-build-script').disabled = false })
  }

  function pollBuild(jid) {
    api('/jobs/' + jid).then(function (j) {
      if (j.state === 'running') { note('Rendering… ' + Math.round((j.seconds || 0)) + 's'); setTimeout(function () { pollBuild(jid) }, 4000); return }
      note(j.state === 'done' ? 'Rendered in ' + Math.round(j.seconds) + 's — open Films to watch it'
                              : 'Render failed: ' + String(j.tail || '').slice(-200))
      $('btn-build-script').disabled = false
      if (window.__studioReloadFilms) window.__studioReloadFilms()
    }).catch(function (e) { note('Job lost: ' + e.message); $('btn-build-script').disabled = false })
  }

  document.addEventListener('DOMContentLoaded', function () {
    var b = $('btn-build-script'); if (b) b.addEventListener('click', build)
    var d = $('btn-draft'); if (d) d.addEventListener('click', draft)
    var s = $('btn-save-script'); if (s) s.addEventListener('click', save)
    render()
  })
})()
