import { useCallback, useEffect, useRef, useState } from 'react'

const WS_URL = 'ws://127.0.0.1:8765'
const SEND_MS = 50        // 20 Hz, matching the bridge's setpoint loop

// Keyed by event.code so it works the same on any keyboard layout.
// Each entry is [axis, contribution] in the drone's body frame.
export const AXIS_KEYS = {
  KeyW: ['vx', 1], KeyS: ['vx', -1],
  KeyA: ['vy', 1], KeyD: ['vy', -1],          // +vy is left
  ArrowUp: ['vz', 1], ArrowDown: ['vz', -1],
  ArrowLeft: ['yaw', 1], ArrowRight: ['yaw', -1],
}

export const ACTION_KEYS = {
  KeyT: 'takeoff',
  KeyL: 'land',
  Space: 'estop',
  Escape: 'estop',
}

const ZERO = { vx: 0, vy: 0, vz: 0, yaw: 0 }

export function useDroneLink() {
  const ws = useRef(null)
  const held = useRef(new Set())
  const [pressed, setPressed] = useState([])     // mirror of `held`, for the UI
  const [axes, setAxes] = useState(ZERO)
  const [telemetry, setTelemetry] = useState(null)
  const [socketState, setSocketState] = useState('connecting')
  const [refused, setRefused] = useState(false)

  const send = useCallback((msg) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg))
    }
  }, [])

  // --- socket, with reconnect ---
  useEffect(() => {
    let socket
    let retry
    let closed = false

    const open = () => {
      socket = new WebSocket(WS_URL)
      ws.current = socket

      socket.onopen = () => { setSocketState('open'); setRefused(false) }
      socket.onmessage = (ev) => {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'state') setTelemetry(msg)
        else if (msg.type === 'refused') setRefused(true)
      }
      socket.onclose = () => {
        setSocketState('closed')
        if (!closed) retry = setTimeout(open, 1000)
      }
      socket.onerror = () => socket.close()
    }

    open()
    return () => { closed = true; clearTimeout(retry); socket?.close() }
  }, [])

  // --- keyboard ---
  useEffect(() => {
    const down = (e) => {
      if (e.repeat) return
      if (e.code in AXIS_KEYS || e.code in ACTION_KEYS) e.preventDefault()

      if (e.code in ACTION_KEYS) {
        send({ type: ACTION_KEYS[e.code] })
        return
      }
      if (e.code in AXIS_KEYS) {
        held.current.add(e.code)
        setPressed([...held.current])
      }
    }

    const up = (e) => {
      if (held.current.delete(e.code)) setPressed([...held.current])
    }

    // Alt-tabbing away with a key down would otherwise leave it stuck on.
    const blur = () => { held.current.clear(); setPressed([]) }

    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [send])

  // --- 20 Hz input packets, sent even when nothing is held ---
  // The bridge treats silence as a lost link, so this doubles as a heartbeat.
  useEffect(() => {
    const id = setInterval(() => {
      const next = { ...ZERO }
      for (const code of held.current) {
        const [axis, amount] = AXIS_KEYS[code]
        next[axis] += amount
      }
      setAxes(next)
      send({ type: 'input', ...next })
    }, SEND_MS)
    return () => clearInterval(id)
  }, [send])

  return {
    telemetry,
    socketState,
    refused,
    pressed,
    axes,
    takeoff: () => send({ type: 'takeoff' }),
    land: () => send({ type: 'land' }),
    estop: () => send({ type: 'estop' }),
    // Reopen the radio link inside the running bridge. Recovers from a
    // Crazyradio that fell off the USB bus without restarting the process —
    // restarting it would drop this very WebSocket.
    reconnect: () => send({ type: 'reconnect' }),
    scan: () => send({ type: 'scan' }),
    connectTo: (uri) => send({ type: 'connect', uri }),
    // Remote power cycle of the drone's STM — clears a latched crash
    // without walking over to it.
    restartDrone: () => send({ type: 'restart' }),
  }
}
