# Quickstart — from nothing to three drones flying

Everything you need is on this page: clone, install, fly. No other file, no
prior setup.

**You need:** Ubuntu 22.04 or 24.04, about 10 GB of free disk, and an internet
connection. Budget **30-40 minutes**, nearly all of it the ArduPilot compile
running unattended.

---

## 1. Clone

```bash
git clone -b simulation https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026.git
cd Swarm_SummerSchool_2026
```

`-b simulation` matters — that is the branch with the simulation on it.

## 2. Install

```bash
./install.sh
```

It asks for your `sudo` password early (system packages), then asks once before
the long compile. Answer `y` and leave it alone. To skip both prompts, use
`./install.sh --yes`.

It installs: the build tools, Gazebo Harmonic and its Python bindings,
ArduPilot SITL, the `ardupilot_gazebo` plugin, and the Python packages. Then it
records where everything went and puts `flyit` on your `PATH`.

**If it fails partway through, just run it again.** Every step checks whether
its own work is already done, so a second run picks up where the first stopped
instead of starting over.

When it finishes it prints a dependency report ending in `RESULT: PASS`.

## 3. Open a new terminal

```bash
flyit --check
```

The install added `~/.local/bin` to your `PATH`, and your **current** terminal
does not know that yet. A new terminal does. If `flyit` is still not found,
use `./flyit --check` from inside the repo directory — it works either way.

You want `RESULT: PASS`. Lines marked `warn` are fine: they cost you a feature,
not the simulation. Lines marked `FAIL` name the fix underneath.

## 4. Fly

```bash
flyit
```

First launch takes a couple of minutes. Four terminal windows open — the
vehicles, the position relay, the flight log, the dashboard — plus a Gazebo
window with three quadrotors on a runway, and a browser at
**http://127.0.0.1:8760**.

If the browser does not open by itself, go to that address manually.

### Then, in the browser

**Wait about a minute first.** GPS and the EKF need to settle, and the buttons
stay disabled until they have. They enable themselves — you do not click
anything to make that happen.

1. **Arm + take off all (20 m)** — all three lift off.
2. **Followers → FOLLOW** — drones 2 and 3 take station behind drone 1.
3. Then any of:
   - **Take control** — fly drone 1 from the keyboard. `W/A/S/D` for
     north/west/south/east, `R/F` up/down, `Q/E` to yaw. The others follow.
   - **Click the map** — drone 1 flies to that point.
   - **Inspect the highway** / **Inspect the whole area** — the swarm surveys
     on its own, finds the staged accident, and reports it with a position and
     a photo.
4. **Land all** when you are done.

Watch the **Station error** chart while the formation settles. That is the
number the whole thing exists to hold.

## 5. Stop

```bash
flyit --stop
```

Closing the windows is not enough — the simulator keeps running. Always stop
it this way, or the next `flyit` will refuse to start on top of it.

---

## Other ways to run it

```bash
flyit 2            # two drones instead of three
flyit --no-gazebo  # no 3D window: headless, starts in seconds, still flies
flyit --no-terms   # no windows at all, everything in the background
flyit --help       # all options
```

`--no-gazebo` is the one to remember on a slow laptop or over SSH. The
formation is ArduPilot's own, so it behaves identically — you just cannot see
it or use the cameras.

## Prove it works, without watching

```bash
flyit                                        # leave it running
python3 scripts/leader_follower.py --vehicles 3    # in another terminal
```

It flies the formation, measures each follower's station error, and prints
`RESULT: PASS` or `RESULT: FAIL`. Expected:

```
  follower1: closest 0.0m, settled 0.0m (tolerance 6m)
  follower2: closest 0.0m, settled 0.0m (tolerance 6m)
RESULT: PASS - every follower held station within 6m.
```

---

## When something goes wrong

Run `flyit --check` first. It separates "your machine is missing something"
from "the simulation is broken", which is most of the problem.

| What you see | What it means |
|---|---|
| `flyit: command not found` | New terminal not opened yet. Use `./flyit` from the repo. |
| `no SITL binary at ...` | The install did not finish. Run `./install.sh` again. |
| `ERROR: vehicles did not come up` | Read `.run/latest/vehicle0.log` — the last lines say why. |
| Dashboard will not load | Something else is on port 8760. `flyit --stop`, or `DASHBOARD_HTTP_PORT=8761 flyit`. |
| Buttons stay greyed out | GPS/EKF not ready. Give it another minute. |
| Camera panels stay black | Gazebo Python bindings missing — `flyit --check` will say so. Everything else still works. |
| It refuses to start, says processes are alive | A previous run is still up. `flyit --stop`. |

Every run writes its own log directory: `.run/latest/` is always the newest,
and `.run/latest/flight_log.txt` is the merged log of all three drones —
modes, arming, and every autopilot message. That file answers most questions
about why a drone did or did not do something.

## Already have ArduPilot built?

Skip the compile. Point at the directory that **contains** your `ardupilot`
checkout:

```bash
export SSS_ROOT=/path/to/parent
./install.sh --no-apt
```

## Going further

- [README.md](README.md) — how it is built, step by step, each step checkable
- [docs/architecture.html](docs/architecture.html) — why it is built that way
- [docs/workshop.html](docs/workshop.html) — the teaching version: diagrams,
  SITL vs HIL, and a slide deck
