/**
 * Video Studio pane for Hermes Desktop.
 *
 * Two rooms behind one route: **Films** (HyperFrames shot compositions — storyboard cards,
 * per-shot re-render, whole-film render with live progress, the hook/motion/legibility gate)
 * and the cutting room, which stays in the web dashboard tab.
 *
 * Everything shown comes back from the plugin's own routes through `ctx.rest`, so the pane
 * and the agent tool read the same numbers. No invented durations, no fake progress.
 */
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { createElement as h, useCallback, useEffect, useRef, useState } from 'react'

const PLUGIN_ID = 'hewn-video-studio'
const PATH = '/video-studio'

const wrap = { width: '100%', height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0,
  background: 'var(--ui-bg, #0b0d10)', color: 'var(--ui-text, #e8eaed)', font: '13px/1.45 system-ui, sans-serif' }
const top = { display: 'flex', alignItems: 'center', gap: 12, padding: '8px 14px',
  borderBottom: '1px solid rgba(127,127,127,.25)' }
const body = { display: 'grid', gridTemplateColumns: '230px minmax(0,1fr) 300px', flex: 1, minHeight: 0 }
const rail = { borderRight: '1px solid rgba(127,127,127,.25)', overflow: 'auto', padding: 8 }
const stage = { display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0, overflow: 'auto' }
const insp = { borderLeft: '1px solid rgba(127,127,127,.25)', overflow: 'auto', padding: 12 }
const btn = { background: 'transparent', color: 'inherit', border: '1px solid rgba(127,127,127,.45)',
  borderRadius: 6, padding: '4px 9px', font: 'inherit', cursor: 'pointer' }
const item = { display: 'block', width: '100%', textAlign: 'left', background: 'transparent', color: 'inherit',
  border: 0, borderLeft: '2px solid transparent', padding: '8px 10px', font: 'inherit', cursor: 'pointer' }
const tag = { fontFamily: 'ui-monospace, monospace', fontSize: 11, opacity: 0.7, letterSpacing: '.08em',
  textTransform: 'uppercase' }
const card = { minWidth: 150, border: '1px solid rgba(127,127,127,.3)', borderRadius: 8, padding: 10,
  background: 'rgba(127,127,127,.06)' }

const ok = '#22c55e'
const bad = '#ef4444'

function fmt(t) {
  if (t == null || Number.isNaN(t)) return '—'
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1)
}

function call(ctx, path, opts) {
  return ctx.rest(path, Object.assign({ timeoutMs: 120000 }, opts || {}))
}

function Films({ ctx }) {
  const [list, setList] = useState([])
  const [id, setId] = useState(null)
  const [film, setFilm] = useState(null)
  const [gate, setGate] = useState(null)
  const [job, setJob] = useState(null)
  const [shot, setShot] = useState(null)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const timer = useRef(null)

  useEffect(() => {
    let alive = true
    call(ctx, '/films')
      .then(r => {
        if (!alive) return
        setList((r && r.films) || [])
        const first = r && r.films && r.films[0]
        if (first) open(first.id)
      })
      .catch(e => { if (alive) setErr(String(e && e.message ? e.message : e)) })
    return () => { alive = false; if (timer.current) clearTimeout(timer.current) }
  }, [])

  const open = useCallback(fid => {
    setId(fid)
    setGate(null)
    setShot(null)
    call(ctx, '/films/' + encodeURIComponent(fid))
      .then(setFilm)
      .catch(e => setErr(String(e && e.message ? e.message : e)))
  }, [ctx])

  const poll = useCallback(jid => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      call(ctx, '/jobs/' + jid)
        .then(j => {
          setJob(j)
          if (j && j.state === 'running') { poll(jid); return }
          if (j && j.state === 'done') {
            setNote('Rendered in ' + j.seconds + 's')
            if (id) open(id)
          } else {
            setErr('Render failed. ' + String((j && j.tail) || '').slice(-300))
          }
        })
        .catch(e => setErr(String(e && e.message ? e.message : e)))
    }, 2000)
  }, [ctx, id, open])

  const renderFilm = () => {
    setErr(''); setNote('')
    call(ctx, '/films/' + encodeURIComponent(id) + '/render', { method: 'POST' })
      .then(r => { setJob({ state: 'running' }); poll(r.job) })
      .catch(e => setErr(String(e && e.message ? e.message : e)))
  }

  const renderShot = bid => {
    setErr(''); setNote('')
    call(ctx, '/films/' + encodeURIComponent(id) + '/beats/' + encodeURIComponent(bid) + '/render', { method: 'POST' })
      .then(r => { setJob({ state: 'running' }); poll(r.job) })
      .catch(e => setErr(String(e && e.message ? e.message : e)))
  }

  const runGate = () => {
    setErr(''); setNote('Running gate…')
    call(ctx, '/films/' + encodeURIComponent(id) + '/gate', { method: 'POST' })
      .then(r => { setGate(r); setNote('') })
      .catch(e => { setGate(null); setNote(''); setErr(String(e && e.message ? e.message : e)) })
  }

  const editShot = bid => {
    setShot({ id: bid, source: 'loading…' })
    call(ctx, '/films/' + encodeURIComponent(id) + '/beats/' + encodeURIComponent(bid) + '/source')
      .then(r => setShot({ id: bid, source: r.source, path: r.path }))
      .catch(e => { setShot(null); setErr(String(e && e.message ? e.message : e)) })
  }

  const saveShot = text => {
    if (!shot) return
    call(ctx, '/films/' + encodeURIComponent(id) + '/beats/' + encodeURIComponent(shot.id) + '/source',
      { method: 'PUT', body: JSON.stringify({ source: text }) })
      .then(() => { setNote('Shot saved — re-render it to see the change'); setShot(null); open(id) })
      .catch(e => setErr(String(e && e.message ? e.message : e)))
  }

  const left = h('div', { style: rail }, [
    h('div', { key: 'h', style: Object.assign({}, tag, { marginBottom: 8 }) }, 'Films'),
    ...list.map(f => h('button', {
      key: f.id, type: 'button', style: Object.assign({}, item,
        f.id === id ? { borderLeftColor: 'var(--ui-accent, #3b82f6)', background: 'rgba(127,127,127,.1)' } : {}),
      onClick: () => open(f.id)
    }, [
      h('div', { key: 't', style: { fontWeight: 500 } }, f.title),
      h('div', { key: 'm', style: tag }, fmt(f.total) + ' · ' + f.beats + ' shots · ' + f.renders.length + ' renders')
    ]))
  ])

  const middle = h('div', { style: stage }, [
    h('div', { key: 'head', style: { display: 'flex', alignItems: 'baseline', gap: 12, padding: '12px 16px 8px' } }, [
      h('div', { key: 't', style: { fontSize: 18, fontWeight: 600 } }, film ? film.title : 'Films'),
      h('div', { key: 'm', style: tag }, film ? fmt(film.total) + ' · ' + film.beats.length + ' shots' : ''),
      h('div', { key: 's', style: { flex: 1 } }),
      film ? h('button', { key: 'r', type: 'button', style: btn, onClick: renderFilm }, 'Render film') : null,
      film ? h('button', { key: 'g', type: 'button', style: Object.assign({}, btn, { marginLeft: 8 }), onClick: runGate }, 'Gate') : null
    ]),
    film ? h('div', { key: 'vid', style: { margin: '0 16px', border: '1px solid rgba(127,127,127,.3)', background: '#000' } },
      film.renders.length
        ? h('video', {
            src: '/api/plugins/' + PLUGIN_ID + '/films/' + encodeURIComponent(film.id) + '/video?t=' + (film.renders[0].mtime || 0),
            controls: true, style: { width: '100%', maxHeight: '46vh', display: 'block' }
          })
        : h('div', { style: { padding: 24, opacity: 0.7 } }, 'No render yet. Edit a shot, then Render film.')) : null,
    film ? h('div', { key: 'shots', style: { display: 'flex', gap: 10, overflowX: 'auto', padding: '12px 16px 16px' } },
      film.beats.map((b, i) => h('div', { key: b.id, style: Object.assign({}, card, b.exists ? {} : { opacity: 0.5 }) }, [
        h('div', { key: 'k', style: tag }, (i + 1) + ' · ' + b.id.replace(/^shot-/, '')),
        h('div', { key: 'd', style: { fontSize: 15, margin: '4px 0' } }, b.duration.toFixed(1) + 's'),
        h('div', { key: 's', style: tag }, b.shot_render ? 'shot rendered' : 'not rendered'),
        h('div', { key: 'a', style: { marginTop: 8, display: 'flex', gap: 6 } }, [
          h('button', { key: 'r', type: 'button', style: Object.assign({}, btn, { fontSize: 11 }), onClick: () => renderShot(b.id) }, 'Re-render'),
          h('button', { key: 'e', type: 'button', style: Object.assign({}, btn, { fontSize: 11 }), onClick: () => editShot(b.id) }, 'Edit')
        ])
      ]))) : null
  ])

  const right = h('div', { style: insp }, [
    note ? h('div', { key: 'n', style: { marginBottom: 10, color: 'var(--ui-accent, #3b82f6)' } }, note) : null,
    err ? h('div', { key: 'e', style: { marginBottom: 10, color: bad, whiteSpace: 'pre-wrap' } }, err) : null,
    job ? h('div', { key: 'j', style: { marginBottom: 10 } },
      'Render: ' + job.state + (job.seconds ? ' · ' + job.seconds + 's' : '')) : null,
    gate ? h('div', { key: 'g' }, [
      h('div', { key: 'h', style: tag }, 'Gate · ' + gate.video),
      h('div', { key: 'v', style: { fontWeight: 600, color: gate.shippable ? ok : bad, margin: '4px 0 10px' } },
        gate.shippable ? 'shippable' : 'failed: ' + (gate.failed || []).join(', ')),
      ...(gate.checks || []).map(c => h('div', { key: c.check, style: { marginBottom: 6, lineHeight: 1.4 } }, [
        h('span', { key: 'p', style: { color: c.pass ? ok : bad } }, (c.pass ? '✓ ' : '✗ ')),
        h('span', { key: 'd' }, c.check + ' — ' + c.detail)
      ]))
    ]) : h('div', { key: 'help', style: { opacity: 0.7, lineHeight: 1.6 } },
      'Gate runs hook, motion and legibility on the latest render. A film only ships when all three pass.'),
    shot ? h('div', { key: 's', style: { marginTop: 14 } }, [
      h('div', { key: 'h', style: tag }, 'Shot · ' + shot.id),
      h('textarea', {
        key: 't', defaultValue: shot.source, spellCheck: false,
        style: { width: '100%', height: 260, fontFamily: 'ui-monospace, monospace', fontSize: 11,
          background: 'rgba(0,0,0,.35)', color: 'inherit', border: '1px solid rgba(127,127,127,.35)',
          borderRadius: 6, padding: 8, marginTop: 6 },
        ref: node => { if (node) node.dataset.shot = shot.id }
      }),
      h('button', {
        key: 'b', type: 'button', style: Object.assign({}, btn, { marginTop: 8, width: '100%' }),
        onClick: () => {
          const node = document.querySelector('textarea[data-shot="' + shot.id + '"]')
          if (node) saveShot(node.value)
        }
      }, 'Save shot')
    ]) : null
  ])

  return h('div', { style: wrap }, [
    h('div', { key: 'top', style: top }, [
      h('div', { key: 'b', style: { fontWeight: 600 } }, 'Hewn Video Studio'),
      h('div', { key: 's', style: { flex: 1 } }),
      h('div', { key: 'h', style: tag }, list.length + ' films')
    ]),
    h('div', { key: 'body', style: body }, [left, middle, right])
  ])
}

export default {
  id: PLUGIN_ID,
  name: 'Video Studio',
  register(ctx) {
    ctx.register({ id: 'films', area: ROUTES_AREA, data: { path: PATH }, render: () => h(Films, { ctx }) })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Video Studio', codicon: 'film' } })
  }
}
