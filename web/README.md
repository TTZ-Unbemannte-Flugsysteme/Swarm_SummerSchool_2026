# Keyboard control from the browser

A React page that flies the Crazyflie with WASD and the arrow keys.

The browser can't reach the Crazyradio over USB, so there are two processes:

```
React UI (:5173)  --WebSocket-->  server.py (:8765)  --cflib/radio-->  Crazyflie
```

## Run it

Two terminals. First the bridge:

```bash
source .venv/bin/activate
python web/server.py
```

Wait for `Connected to radio://...`. Then the UI:

```bash
cd web/ui
npm install     # first time only
npm run dev
```

Open http://localhost:5173. Click the page once so it has keyboard focus.

## Keys

| Key | Does |
| --- | --- |
| `W` / `S` | forward / back |
| `A` / `D` | left / right |
| `↑` / `↓` | up / down |
| `←` / `→` | yaw left / right |
| `T` | take off to 0.4 m |
| `L` | land and disarm |
| `Space` or `Esc` | emergency stop — cuts the motors, it drops |

Movement is intentionally gentle (0.4 m/s, 90 °/s). The Flow deck tracks slow
motion much better than fast, and the limits are the constants at the top of
`server.py` if you want to change them.

## When the radio drops

The Crazyradio sometimes falls off the USB bus mid-session. Two buttons
recover from that without restarting the bridge:

- **Reconnect radio** — closes the link and opens it again.
- **Find radio** — closes the link and scans, the same way `1_scan.py` does.
  Anything it finds is listed underneath; click one to connect to it.
- **Restart drone** — power-cycles the drone over the radio, then reconnects.

A failed link also reopens itself every few seconds, so power-cycling the
drone by hand no longer leaves Take off greyed out.

All three need the radio to themselves, so they only work with the drone on
the ground — pressing one mid-flight just says "land first". That guard
matters most for Restart drone: cutting power to the motors in mid-air is a
fall, not a restart.

## Restart drone

The usual reason Take off refuses is a latched crash or tumble — the
supervisor reports `Is crashed` and will not arm until the drone reboots and
redoes its sensor self-test. Restart drone does that remotely:
`PowerSwitch(URI).stm_power_cycle()` drops the STM and the decks and brings
them back. The NRF keeps the radio alive throughout, so it returns on the same
address.

It then waits `REBOOT_WAIT` (8 s) before reconnecting, because the self-test
only completes if the drone is **still and level** while it runs. Move it
during those 8 seconds and it will come back refusing to arm again.

There is deliberately no "restart the server" button. The bridge is what
serves the WebSocket the button would travel over, so restarting it would cut
the UI off from the thing meant to bring it back. Reopening the link is what
actually fixes a vanished dongle; if the bridge process itself has died the
page says **bridge offline**, and that one needs the terminal.

A found URI that differs from `uri.py` connects for this session only. Paste
it into `uri.py` to make it stick.

## What keeps it from flying away

The UI sends an input packet 20 times a second even when no key is down, so
silence means something broke:

- **0.5 s with no packet** — velocities go to zero and it hovers in place.
- **2 s with no packet** — it lands itself and disarms.

So closing the tab, a Wi-Fi hiccup or a crashed UI all end with it on the
floor rather than in a wall. Only one tab may hold the controls; a second is
refused instead of the two fighting over the setpoints.

Height is clamped to 0.15–1.0 m, and takeoff is refused unless the supervisor
reports it can be armed.

`ctrl-C` on the bridge lands it too, but the power switch is still the real
emergency stop.

## Before you fly

Same rules as the scripts: textured matte floor for the Flow deck, 2×2 m
clear, and run `python 2_preflight.py` first if it's been sitting.
