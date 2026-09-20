# Crazyflie 2.1 Brushless — minimal macOS starter

Four small scripts on top of Bitcraze's official `cflib`. No framework, no ROS.

## Setup (once)

```bash
cd crazyflie-start
chmod +x setup.sh
./setup.sh
```

This installs `libusb` via Homebrew (the Crazyradio driver needs it on macOS),
creates a `.venv`, and installs `cflib` + `cfclient`.

In every new terminal afterwards:

```bash
source .venv/bin/activate
```

## Before the first flight

The 2.1 Brushless ships with firmware that is usually a few releases behind.
Flash the latest once with the GUI client:

```bash
cfclient
```

Connect, then **Connect → Bootloader**, and flash the latest release. While
you're there, check the Flow deck shows up under the Debug/Parameters tab.

## Run order

```bash
python 1_scan.py        # find the radio URI
python 2_preflight.py   # deck, battery, supervisor state — nothing spins
python 3_hover.py       # take off, hover 5 s at 0.4 m, land
python 4_square.py      # 0.5 m square
```

If `1_scan.py` prints a URI other than `radio://0/80/2M/E7E7E7E7E7`, paste it
into `uri.py`.

To fly it by hand from a browser instead, see `web/README.md` — a React page
with WASD/arrow-key control on top of a small WebSocket bridge.

## Things specific to the brushless model

- **It must be armed.** Unlike the old 2.1, the brushless refuses all setpoints
  until `cf.supervisor.send_arming_request(True)` is sent. Every script here
  does that, and disarms at the end.
- **Startup self-test.** After powering on, leave it still and level for a few
  seconds. If you move it during the sensor calibration it will not arm.
- **Stiff motors at low throttle** on the first spin-ups is normal.
- **Propeller direction matters.** CW props are marked `55-35R`, CCW `55-35`;
  convex side up, sharper corner trailing the rotation.

## Flying safely indoors

Flow deck v2 measures motion optically, so it needs a **textured, matte floor**
— carpet, a patterned rug, newspaper. Over plain white lino or glossy tile it
drifts badly. Keep flights under ~0.6 m to start, and give it 2×2 m clear.

`ctrl-C` during a script triggers the land-and-disarm path, but a hand on the
power switch is the real emergency stop.

## Where to go next

The official examples repo has much more — logging, swarms, high-level
commander, Lighthouse:

```bash
git clone https://github.com/bitcraze/crazyflie-lib-python
ls crazyflie-lib-python/examples
```
