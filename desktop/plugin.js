/**
 * Video Studio pane for Hermes Desktop.
 *
 * Frames the studio app the loopback host serves, so the pane and the agent tool look at the
 * same films — the shape Music Studio uses, with its own room: a film rail, a stage, a
 * storyboard strip and an inspector that shows the gate's verdict.
 *
 * Two ways to find the host, on purpose: `GET /studio/host` through `ctx.rest` (authoritative,
 * and starts the host when it is not running), then the `studio-host.json` the launcher writes.
 */
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { createElement as h, useCallback, useEffect, useState } from 'react'

const PLUGIN_ID = 'hewn-video-studio'
const PATH = '/video-studio'
const STATE_REL = ['plugin-data', 'hewn-video-studio', 'studio-host.json']

const wrap = { width: '100%', height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }
const frame = { flex: 1, width: '100%', border: 'none', display: 'block', background: 'transparent' }
const bar = { display: 'flex', alignItems: 'center', gap: 12, padding: '6px 10px',
  borderTop: '1px solid rgba(127,127,127,.25)', fontSize: 12 }
const notice = { padding: 24, maxWidth: 640, lineHeight: 1.5 }
const detail = { opacity: .7, fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap' }
const linkButton = { background: 'transparent', border: 'none', color: 'inherit', font: 'inherit',
  fontSize: 12, cursor: 'pointer', textDecoration: 'underline', padding: 0 }

async function hostFromDisk() {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
  const rootFn = bridge && (bridge.desktopPluginsRoot || (bridge.fs && bridge.fs.desktopPluginsRoot))
  if (!bridge || typeof bridge.readFileText !== 'function' || typeof rootFn !== 'function') return null
  const root = String((await rootFn()) || '')
  const home = root.replace(/[\\/]desktop-plugins[\\/]?$/, '')
  if (!home || home === root) return null
  for (const parts of [STATE_REL, ['profiles', 'default'].concat(STATE_REL)]) {
    try {
      const file = await bridge.readFileText(`${home}/${parts.join('/')}`)
      const text = String((file && file.text) || '').trim()
      if (!text) continue
      const port = Number(JSON.parse(text).port)
      if (Number.isInteger(port) && port > 0 && port < 65536) return `http://127.0.0.1:${port}`
    } catch {
      /* no state file yet */
    }
  }
  return null
}

function Studio({ ctx }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const resolve = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      if (ctx && typeof ctx.rest === 'function') {
        const answer = await ctx.rest('/studio/host', { timeoutMs: 45000 })
        if (answer && answer.url) { setUrl(answer.url); setBusy(false); return }
        if (answer && answer.detail) setError(String(answer.detail))
      }
    } catch (err) {
      setError(String((err && err.message) || err))
    }
    const disk = await hostFromDisk()
    setBusy(false)
    if (disk) setUrl(disk)
    else setError(prev => prev || 'The studio host is not running yet.')
  }, [ctx])

  useEffect(() => { resolve() }, [resolve])

  const openExternal = () => {
    const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
    if (bridge && typeof bridge.openExternal === 'function') bridge.openExternal(url)
    else if (typeof window !== 'undefined') window.open(url, '_blank', 'noopener')
  }

  if (!url) {
    return h('div', { style: Object.assign({}, wrap, notice) }, [
      h('h2', { key: 't', style: { fontSize: 16, margin: '0 0 8px' } }, 'Video Studio'),
      h('p', { key: 'p', style: { opacity: .75, margin: '0 0 10px' } },
        busy ? 'Starting the studio…' : 'The studio app is served locally.'),
      error ? h('p', { key: 'e', style: detail }, error) : null,
      h('button', {
        key: 'r', type: 'button', onClick: resolve,
        style: { marginTop: 8, padding: '6px 12px', cursor: 'pointer' }
      }, 'Try again')
    ])
  }

  return h('div', { style: wrap }, [
    h('iframe', { key: 'studio', src: `${url}/`, title: 'Video Studio',
      allow: 'autoplay; clipboard-write', style: frame }),
    h('div', { key: 'bar', style: bar }, [
      h('span', { key: 'w', style: { opacity: .6 } }, `${url} · same films the agent edits`),
      h('button', { key: 'o', type: 'button', style: linkButton, onClick: openExternal }, 'Open in a browser ↗'),
      h('button', { key: 'r', type: 'button', style: linkButton, onClick: resolve }, 'Reload')
    ])
  ])
}

export default {
  id: PLUGIN_ID,
  name: 'Video Studio',
  register(ctx) {
    ctx.register({ id: 'studio', area: ROUTES_AREA, data: { path: PATH }, render: () => h(Studio, { ctx }) })
    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { path: PATH, label: 'Video Studio', codicon: 'device-camera-video' }
    })
  }
}
