"""Keyboard-control bridge: browser <-> WebSocket <-> Crazyflie.

The browser cannot talk to the Crazyradio itself, so this process holds the
cflib link and translates JSON messages from the React UI into setpoints.

    source .venv/bin/activate
    python web/server.py

Then start the UI (web/ui) and open it. One browser tab at a time — a second
one is refused rather than allowed to fight over the controls.

The radio link can be dropped and reopened without restarting this process,
which is what the UI's Reconnect and Find radio buttons do. A Crazyradio that
falls off the USB bus mid-session is common enough to be worth recovering from
in place.

Safety, because this flies a real drone from a web page:
  - the UI sends an input packet ~20x a second, even when no key is down
  - no packet for INPUT_TIMEOUT  -> velocities go to zero, it just hovers
  - no packet for LINK_TIMEOUT   -> it lands itself and disarms
  - closing the tab counts as losing the link, so it lands
"""
import asyncio
import json
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cflib.crtp
from cflib.crazyflie import Crazyflie
from cflib.crazyflie.log import LogConfig
from cflib.crazyflie.syncCrazyflie import SyncCrazyflie
from cflib.utils.power_switch import PowerSwitch

from uri import URI

HOST = '127.0.0.1'
PORT = 8765

TICK = 0.05           # seconds between setpoints (20 Hz)

# How far the normalised -1..1 axes from the keyboard actually move it.
# Deliberately gentle; the Flow deck tracks slow motion far better than fast.
MAX_SPEED = 0.4       # m/s forward/back/left/right at full stick
MAX_CLIMB = 0.3       # m/s up/down
MAX_YAW = 90.0        # deg/s

HOVER_HEIGHT = 0.4    # m, where takeoff leaves it
MIN_HEIGHT = 0.15     # m, floor for the climb/descend keys
MAX_HEIGHT = 1.00     # m, ceiling — keep it indoors-sane
TAKEOFF_TIME = 1.5    # s to ramp up to HOVER_HEIGHT
LAND_TIME = 1.5       # s to ramp back down

INPUT_TIMEOUT = 0.5   # s without input -> stop moving
LINK_TIMEOUT = 2.0    # s without input -> land
RETRY_DELAY = 5.0     # s between automatic attempts to reopen a lost link
REBOOT_WAIT = 8.0     # s to let the drone finish its self-test after a restart

FACTORY_ADDRESS = 'E7E7E7E7E7'

IDLE, TAKEOFF, FLYING, LANDING = 'idle', 'takeoff', 'flying', 'landing'

# Requests that need the radio to themselves, so they can only run with the
# link closed and the drone on the ground.
LINK_REQUESTS = ('reconnect', 'connect', 'scan', 'restart')


def friendly(exc):
    """cflib puts a whole traceback in the message; keep the actionable line."""
    detail = str(exc).strip().splitlines()[0]
    if 'Crazyradio' in detail:
        return ('No Crazyradio dongle found — reseat it, ideally in another USB '
                'port with no hub.')
    if 'Too many packets lost' in detail or 'timeout' in detail.lower():
        return 'The drone did not answer — switch it on and check the battery.'
    if 'Failed to connect to Crazyflie' in detail:
        return ('The drone did not answer the restart — check it is switched on '
                'and in range.')
    return detail


class Pilot(threading.Thread):
    """Owns the radio link. Runs the setpoint loop on its own thread.

    cflib is synchronous and wants a thread of its own, so the asyncio side
    never touches the Crazyflie directly — it only drops requests into the
    queue and reads the snapshot dict.
    """

    daemon = True

    def __init__(self):
        super().__init__()
        self.requests = queue.Queue()
        self.lock = threading.Lock()
        self.axes = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0, 'yaw': 0.0}
        self.last_input = 0.0
        self.uri = URI
        self.snapshot = {'state': IDLE, 'link': 'connecting', 'target': 0.0,
                         'height': 0.0, 'vbat': 0.0, 'armed': False,
                         'note': '', 'uri': URI, 'radios': []}
        self.stop_flag = threading.Event()

    # ---- called from the asyncio side -------------------------------------

    def set_axes(self, vx, vy, vz, yaw):
        with self.lock:
            self.axes = {'vx': _clamp(vx), 'vy': _clamp(vy),
                         'vz': _clamp(vz), 'yaw': _clamp(yaw)}
            self.last_input = time.time()

    def go_quiet(self):
        """Browser gone: stop feeding input and let the watchdog take over."""
        with self.lock:
            self.axes = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0, 'yaw': 0.0}
            self.last_input = 0.0

    def request(self, cmd, **extra):
        self.requests.put({'cmd': cmd, **extra})

    def read(self):
        with self.lock:
            return dict(self.snapshot)

    def _update(self, **kw):
        with self.lock:
            self.snapshot.update(kw)

    # ---- the flying thread -------------------------------------------------

    def run(self):
        cflib.crtp.init_drivers()
        open_link = True                      # connect once on startup
        retry_at = None

        while not self.stop_flag.is_set():
            if open_link:
                open_link = False
                retry_at = None
                self._session()               # blocks while the link is up
                # A link that *failed* comes back by itself — power-cycling the
                # drone or a dongle dropping off USB shouldn't leave the UI
                # stuck with Take off greyed out until someone presses
                # Reconnect. A link we closed on purpose stays closed.
                if self.read()['link'] == 'error':
                    retry_at = time.time() + RETRY_DELAY
                    self._update(note=f"{self.read()['note']} Retrying "
                                      f"every {RETRY_DELAY:.0f}s...")
                continue

            req = self._take(timeout=0.2)
            if not req:
                if retry_at and time.time() >= retry_at:
                    self._update(note='Link down — retrying...')
                    open_link = True
                continue

            cmd = req['cmd']
            if cmd == 'reconnect':
                open_link = True
            elif cmd == 'connect':
                self.uri = req.get('uri') or URI
                self._update(uri=self.uri)
                open_link = True
            elif cmd == 'scan':
                self._scan()
            elif cmd == 'restart':
                self._restart_drone()
                open_link = True

    def _take(self, timeout=None):
        try:
            if timeout is None:
                return self.requests.get_nowait()
            return self.requests.get(timeout=timeout)
        except queue.Empty:
            return None

    def _scan(self):
        """Look for Crazyflies. Only safe with our own link closed."""
        self._update(note='Scanning for Crazyflies...', radios=[])
        try:
            found = [u for u, _ in cflib.crtp.scan_interfaces()]
            # The scan only probes the factory address, so if uri.py points
            # somewhere else, look there too — same as 1_scan.py.
            own = self.uri.rsplit('/', 1)[-1]
            if own.upper() != FACTORY_ADDRESS:
                found += [u for u, _ in cflib.crtp.scan_interfaces(int(own, 16))
                          if u not in found]
        except Exception as exc:                       # noqa: BLE001
            self._update(link='error', radios=[], note=friendly(exc))
            print(f'Scan failed: {friendly(exc)}')
            return

        if found:
            self._update(radios=found,
                         note=f'Found {len(found)}. Click one to connect.')
        else:
            self._update(radios=[],
                         note='Nothing answered. Is the drone switched on and charged?')
        print(f'Scan found: {found or "nothing"}')

    def _restart_drone(self):
        """Power-cycle the drone over the radio.

        Clears a latched crash or tumble without anyone walking over to flip
        the switch. Only the STM and the decks reboot — the NRF keeps the
        radio alive, so it comes back on the same address.

        Needs the radio to itself, so run() only calls this with our own link
        already closed, and _fly refuses it outright while airborne: cutting
        power to the motors in mid-air is a fall, not a restart.
        """
        self._update(link='offline', state=IDLE, armed=False, radios=[],
                     note='Power-cycling the drone...')
        print('Power-cycling the drone...')

        switch = None
        try:
            switch = PowerSwitch(self.uri)
            switch.stm_power_cycle()
        except Exception as exc:                       # noqa: BLE001
            self._update(link='error', note=friendly(exc))
            print(f'Power cycle failed: {friendly(exc)}')
            return
        finally:
            if switch:
                switch.close()

        # It reboots into its sensor self-test and will refuse to arm until
        # that finishes — and it only finishes if left still and level.
        self._update(note=f'Rebooting. Leave it still and level '
                          f'for {REBOOT_WAIT:.0f}s.')
        print(f'Power-cycled. Waiting {REBOOT_WAIT:.0f}s for the self-test.')
        self.stop_flag.wait(REBOOT_WAIT)

    def _session(self):
        """Open the link, fly until it closes, then report why it closed."""
        self._update(link='connecting', radios=[], state=IDLE, armed=False,
                     note=f'Opening {self.uri}...')
        try:
            cache = str(Path(__file__).resolve().parent.parent / 'cache')
            with SyncCrazyflie(self.uri, cf=Crazyflie(rw_cache=cache)) as scf:
                self._fly(scf)
        except Exception as exc:                       # noqa: BLE001
            self._update(link='error', state=IDLE, armed=False, note=friendly(exc))
            print(f'Link failed: {friendly(exc)}')
            return

        # _fly returned on its own terms. Don't clobber an error it already set.
        if not self.stop_flag.is_set() and self.read()['link'] != 'error':
            self._update(link='offline', state=IDLE, armed=False,
                         note='Link closed. Press Reconnect when ready.')

    def _fly(self, scf):
        cf = scf.cf
        time.sleep(1.0)

        if not int(cf.param.get_value('deck.bcFlow2')):
            self._update(link='error',
                         note='No Flow deck v2 — it cannot hold position.')
            return

        # If the radio drops we must stop pushing setpoints at nothing, and
        # the UI has to hear about it rather than sitting on "connecting".
        link_down = threading.Event()

        def lost(_uri, msg):
            link_down.set()
            self._update(link='error', state=IDLE, armed=False,
                         note=f'Radio link lost ({msg}).')
            print(f'Radio link lost: {msg}')

        cf.connection_lost.add_callback(lost)

        self._start_logging(scf)
        self._update(link='connected', note='Ready. Press T to take off.')
        print(f'Connected to {self.uri}. UI can connect now.')

        state = IDLE
        target_z = 0.0
        phase_start = 0.0
        armed = False

        while not (self.stop_flag.is_set() or link_down.is_set()):
            loop_start = time.time()

            with self.lock:
                axes = dict(self.axes)
                silent_for = loop_start - self.last_input

            # Watchdog. Losing the browser must not leave it flying.
            if state in (TAKEOFF, FLYING) and silent_for > INPUT_TIMEOUT:
                axes = {'vx': 0.0, 'vy': 0.0, 'vz': 0.0, 'yaw': 0.0}
                if silent_for > LINK_TIMEOUT:
                    state, phase_start = LANDING, loop_start
                    self._update(note='Lost the browser — landing.')

            req = self._take()
            cmd = req['cmd'] if req else None

            if cmd in LINK_REQUESTS:
                # These need the radio free. Only once it is on the ground.
                if state != IDLE or armed:
                    self._update(note='Land first — the radio is busy flying.')
                else:
                    self.requests.put(req)     # run() does it with the link shut
                    print(f'Closing link for: {cmd}')
                    return

            elif cmd == 'estop':
                cf.commander.send_stop_setpoint()
                cf.supervisor.send_arming_request(False)
                armed, state, target_z = False, IDLE, 0.0
                self._update(state=IDLE, armed=False, note='EMERGENCY STOP.')

            elif cmd == 'takeoff' and state == IDLE:
                if not cf.supervisor.can_be_armed:
                    self._update(note='Refuses to arm — press Restart drone, then leave '
                                      'it still and level.')
                else:
                    cf.supervisor.send_arming_request(True)
                    time.sleep(0.5)
                    # The low-level commander stays locked until it sees a
                    # zero setpoint, so send one before anything else.
                    cf.commander.send_setpoint(0.0, 0.0, 0, 0)
                    armed = True
                    state, phase_start, target_z = TAKEOFF, loop_start, 0.0
                    self._update(state=TAKEOFF, armed=True, note='Taking off...')

            elif cmd == 'land' and state in (TAKEOFF, FLYING):
                state, phase_start = LANDING, loop_start
                self._update(state=LANDING, note='Landing...')

            # --- drive the drone for this tick ---
            if state == TAKEOFF:
                frac = min((loop_start - phase_start) / TAKEOFF_TIME, 1.0)
                target_z = HOVER_HEIGHT * frac
                cf.commander.send_hover_setpoint(0.0, 0.0, 0.0, target_z)
                if frac >= 1.0:
                    state = FLYING
                    self._update(state=FLYING, note='Flying. WASD / arrows.')

            elif state == FLYING:
                target_z = _clamp(target_z + axes['vz'] * MAX_CLIMB * TICK,
                                  MIN_HEIGHT, MAX_HEIGHT)
                cf.commander.send_hover_setpoint(
                    axes['vx'] * MAX_SPEED,
                    axes['vy'] * MAX_SPEED,
                    axes['yaw'] * MAX_YAW,
                    target_z)

            elif state == LANDING:
                frac = min((loop_start - phase_start) / LAND_TIME, 1.0)
                z = max(target_z * (1.0 - frac), 0.05)
                cf.commander.send_hover_setpoint(0.0, 0.0, 0.0, z)
                if frac >= 1.0:
                    cf.commander.send_stop_setpoint()
                    cf.supervisor.send_arming_request(False)
                    armed, state, target_z = False, IDLE, 0.0
                    self._update(state=IDLE, armed=False, note='Landed. Disarmed.')

            self._update(target=round(target_z, 2))
            time.sleep(max(0.0, TICK - (time.time() - loop_start)))

        # Told to quit: put it down rather than freezing mid-air. Pointless
        # (and throws) if the radio is what went away in the first place.
        if not link_down.is_set() and (armed or state != IDLE):
            cf.commander.send_stop_setpoint()
            cf.supervisor.send_arming_request(False)
            print('Disarmed.')

    def _start_logging(self, scf):
        lg = LogConfig(name='ui', period_in_ms=200)
        lg.add_variable('pm.vbat', 'float')
        lg.add_variable('stateEstimate.z', 'float')

        def got(_ts, data, _cfg):
            # height is what it actually measures; target is what we asked for.
            self._update(vbat=round(data['pm.vbat'], 2),
                         height=round(data['stateEstimate.z'], 2))

        lg.data_received_cb.add_callback(got)
        scf.cf.log.add_config(lg)
        lg.start()


def _clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, float(v)))


# ---- websocket side --------------------------------------------------------

pilot = Pilot()
client_connected = threading.Event()


async def handle(ws):
    if client_connected.is_set():
        await ws.send(json.dumps({'type': 'refused',
                                  'note': 'Another tab already has the controls.'}))
        await ws.close()
        return

    client_connected.set()
    print('UI connected.')
    telemetry = asyncio.create_task(push_telemetry(ws))
    try:
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get('type')
            if kind == 'input':
                pilot.set_axes(msg.get('vx', 0), msg.get('vy', 0),
                               msg.get('vz', 0), msg.get('yaw', 0))
            elif kind == 'connect':
                uri = msg.get('uri')
                pilot.request('connect', uri=uri if isinstance(uri, str) else None)
            elif kind in ('takeoff', 'land', 'estop', 'reconnect', 'scan',
                          'restart'):
                pilot.request(kind)
    except websockets.ConnectionClosed:
        pass
    finally:
        telemetry.cancel()
        client_connected.clear()
        pilot.go_quiet()
        print('UI disconnected — watchdog will land it if airborne.')


async def push_telemetry(ws):
    while True:
        await ws.send(json.dumps({'type': 'state', **pilot.read()}))
        await asyncio.sleep(0.1)


async def main():
    pilot.start()

    # Being killed without closing the link cleanly leaves the drone believing
    # it still has one, and the next run then fails with "could not send packet
    # to copter". So catch both signals and let the pilot thread unwind.
    loop = asyncio.get_running_loop()
    stopping = loop.create_future()

    def ask_stop():
        if not stopping.done():
            stopping.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, ask_stop)

    async with websockets.serve(handle, HOST, PORT):
        print(f'Bridge listening on ws://{HOST}:{PORT}')
        await stopping

    print('\nShutting down — landing if airborne, then closing the link.')
    pilot.stop_flag.set()
    pilot.join(timeout=LAND_TIME + 3.0)


if __name__ == '__main__':
    asyncio.run(main())
