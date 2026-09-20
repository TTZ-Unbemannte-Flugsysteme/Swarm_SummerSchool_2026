import { useDroneLink } from './useDroneLink'

const BAT_EMPTY = 3.0
const BAT_FULL = 4.2

// The on-screen key map. Each cell names the event.code it lights up for.
const MOVE_PAD = [
  [null, { code: 'KeyW', cap: 'W', label: 'forward' }, null],
  [{ code: 'KeyA', cap: 'A', label: 'left' },
   { code: 'KeyS', cap: 'S', label: 'back' },
   { code: 'KeyD', cap: 'D', label: 'right' }],
]

const ALT_PAD = [
  [null, { code: 'ArrowUp', cap: '↑', label: 'up' }, null],
  [{ code: 'ArrowLeft', cap: '←', label: 'yaw left' },
   { code: 'ArrowDown', cap: '↓', label: 'down' },
   { code: 'ArrowRight', cap: '→', label: 'yaw right' }],
]

export default function App() {
  const { telemetry, socketState, refused, pressed, takeoff, land, estop,
          reconnect, scan, connectTo, restartDrone } = useDroneLink()

  const link = telemetry?.link ?? 'offline'
  const state = telemetry?.state ?? 'idle'
  const airborne = state === 'takeoff' || state === 'flying'
  const vbat = telemetry?.vbat ?? 0
  const batPct = Math.max(0, Math.min(100,
    ((vbat - BAT_EMPTY) / (BAT_FULL - BAT_EMPTY)) * 100))

  const status =
    socketState !== 'open' ? { tone: 'bad', text: 'bridge offline' }
    : refused ? { tone: 'bad', text: 'another tab has the controls' }
    : link === 'error' ? { tone: 'bad', text: 'link error' }
    : link === 'offline' ? { tone: 'warn', text: 'link closed' }
    : link === 'connecting' ? { tone: 'warn', text: 'connecting to drone' }
    : { tone: 'good', text: 'connected' }

  // Both need the radio to themselves, so only on the ground.
  const radioBusy = airborne || socketState !== 'open'
  const radios = telemetry?.radios ?? []

  return (
    <div className="app">
      <header>
        <h1>Crazyflie Manual Control</h1>
        <span className={`status ${status.tone}`}>
          <i /> {status.text}
        </span>
      </header>

      <section className="readouts">
        <Readout label="State" value={state} accent={airborne} />
        <Readout label="Height" value={`${(telemetry?.height ?? 0).toFixed(2)} m`} />
        <Readout label="Target" value={`${(telemetry?.target ?? 0).toFixed(2)} m`} />
        <Readout label="Armed" value={telemetry?.armed ? 'yes' : 'no'} accent={telemetry?.armed} />
        <div className="readout">
          <span className="label">Battery</span>
          <span className="value">{vbat ? `${vbat.toFixed(2)} V` : '—'}</span>
          <div className="bar"><div style={{ width: `${batPct}%` }} /></div>
        </div>
      </section>

      <p className="note">{telemetry?.note || 'Waiting for the bridge...'}</p>

      <section className="pads">
        <Pad title="Move" rows={MOVE_PAD} pressed={pressed} />
        <Pad title="Altitude & yaw" rows={ALT_PAD} pressed={pressed} />
      </section>

      <section className="actions">
        <button onClick={takeoff} disabled={airborne || link !== 'connected'}>
          Take off <kbd>T</kbd>
        </button>
        <button onClick={land} disabled={!airborne}>
          Land <kbd>L</kbd>
        </button>
        <button className="estop" onClick={estop}>
          Emergency stop <kbd>Space</kbd>
        </button>
      </section>

      <section className="link-row">
        <button className="ghost" onClick={reconnect} disabled={radioBusy}>
          Reconnect radio
        </button>
        <button className="ghost" onClick={scan} disabled={radioBusy}>
          Find radio
        </button>
        <button className="ghost warn" onClick={restartDrone} disabled={radioBusy}>
          Restart drone
        </button>
        <span className="uri">{telemetry?.uri ?? ''}</span>
      </section>

      {radios.length > 0 && (
        <section className="radios">
          {radios.map((uri) => (
            <button key={uri} className="ghost found" onClick={() => connectTo(uri)}
                    disabled={radioBusy}>
              {uri}
            </button>
          ))}
        </section>
      )}

      <footer>
        Motors cut instantly on emergency stop — it will drop. Use it only when
        landing normally is worse. Close this tab and it lands by itself.
        Reconnect, Find radio and Restart drone all need the drone on the
        ground. Restart drone power-cycles it over the radio — use it to clear
        a crashed state, then leave it still and level while it self-tests.
      </footer>
    </div>
  )
}

function Readout({ label, value, accent }) {
  return (
    <div className="readout">
      <span className="label">{label}</span>
      <span className={`value${accent ? ' accent' : ''}`}>{value}</span>
    </div>
  )
}

function Pad({ title, rows, pressed }) {
  return (
    <div className="pad">
      <h2>{title}</h2>
      <div className="grid">
        {rows.flatMap((row, r) =>
          row.map((key, c) =>
            key ? (
              <div key={`${r}-${c}`}
                   className={`key${pressed.includes(key.code) ? ' on' : ''}`}>
                <span className="cap">{key.cap}</span>
                <span className="fn">{key.label}</span>
              </div>
            ) : <div key={`${r}-${c}`} className="key blank" />
          )
        )}
      </div>
    </div>
  )
}
