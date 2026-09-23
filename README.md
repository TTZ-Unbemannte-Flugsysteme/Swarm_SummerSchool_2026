# Swarm-Simulation-SSS

Leader-follower simulation for the F450 swarm, built on ArduPilot SITL and
Gazebo. **The simulation runs on the NUT-HMT PC** — this repository is the
source; `flyit` brings the whole thing up there.

![Gazebo showing three quadrotors over the runway beside the operator dashboard with live telemetry, three camera feeds and the formation map](SC/Screenshot%20from%202026-09-21%2013-07-33.png)

*Three drones in Gazebo and the operator dashboard, mid-session: telemetry and
a camera per drone, keyboard control of the leader, the inspection surveys, and
the formation holding 0.0 m station error.*

Design rationale lives in [docs/architecture.html](docs/architecture.html);
this file is the build, in the order you can check it. To *teach* it, open
[docs/workshop.html](docs/workshop.html) — diagrams, SITL vs HIL, why ports
decide everything, and a 16-slide deck with a presenter mode.

Every step prints `RESULT: PASS` or `RESULT: FAIL` and exits non-zero on
failure, so nothing here needs to be judged by eye.

## Everything at once

```bash
flyit              # 3 drones in Gazebo + flight log + dashboard
flyit 2            # two drones
flyit --no-gazebo  # headless physics, starts much faster
flyit --no-terms   # background everything, no windows
flyit --mp         # also start Mission Planner
flyit --stop       # shut it all down
```

`flyit` is on `PATH` via a symlink, so it works from any directory:

```bash
ln -sfn "$PWD/flyit" ~/.local/bin/flyit    # already done on this machine
```

It resolves that symlink before doing anything, so it always finds the repo
rather than the symlink's own directory. Session logs land in the repo, never
in whatever directory you happened to be in. Remove the symlink to undo;
`./flyit` from the repo works either way.

It opens four terminal windows - the vehicles (with Gazebo and a MAVProxy
console per drone), the position relay, the unified flight log, and the
dashboard server - then points a browser at the status page.

`flyit` clears any previous run, generates the Gazebo world, starts the
vehicles, waits for real heartbeats (not just for ports to exist), starts the
position relay, flight log and dashboard, then opens
<http://127.0.0.1:8760>.

The dashboard shows, live: one card per vehicle with mode, altitude, attitude,
climb rate, EKF health, GPS and armed state; a camera view from each drone; a
top-down formation plot with each follower's commanded station drawn as a ring
and the error as a line to it; and a station-keeping error chart over the last
three minutes. It derives which vehicle is the leader from the followers'
`FOLL_SYSID` rather than assuming, so a mis-pointed parameter shows up on
screen instead of hiding.

It also flies the leader. See [Flying the leader from the
browser](#flying-the-leader-from-the-browser) below; the design is drawn as
three sequence diagrams in
[docs/architecture.html](docs/architecture.html) section 10.

With the swarm up, fly the evaluation and watch the error chart converge:

```bash
python3 scripts/leader_follower.py --vehicles 3
```

Three drones fly a trailing V: the leader, plus followers at -25 m north
&pm;20 m east. Measured result, all three in Gazebo:

```
      4        5m         37.1m err         20.4m err
     12       79m          1.2m err          0.1m err
     20      120m          0.0m err          0.0m err
  follower1: closest 0.0m, settled 0.0m (tolerance 6m)
  follower2: closest 0.0m, settled 0.0m (tolerance 6m)
RESULT: PASS - every follower held station within 6m.
```

### Flight logs, one set per session

Every launch stamps a session directory, so a run never destroys the logs of
the one before it:

```
.run/sessions/20260916-155310/
    flight_log.txt      merged autopilot log for all drones
    vehicle0.log        SITL + MAVProxy stdout, per vehicle
    relay.log
    dashboard.log
    launch.out
    session_info.txt    when it started, on what host, against which ArduPilot
.run/latest -> sessions/20260916-155310      always the newest
```

`flyit` stamps the session once and exports it, so every component it starts
writes into the same directory rather than each inventing its own. Running a
script by hand makes its own session.

The merged flight log carries wall-clock time *and* seconds since start, so it
lines up against both real events and the dataflash logs:

```
15:54:03    30.5s drone 1  MODE      STABILIZE -> GUIDED
15:54:04    30.9s drone 1  ARMED     in GUIDED
15:54:08    35.0s drone 1  DATAFLASH .run/v0/logs/00000001.BIN
15:54:17    44.6s drone 2  MODE      GUIDED -> FOLLOW
```

Dataflash `.BIN` files live in shared per-vehicle directories, so rather than
guess which belong to a session, the log names each one as ArduPilot opens it.
They only appear once a vehicle arms - their absence is itself informative.

List past sessions with `ls .run/sessions/`.

The merged log is what diagnoses a refusal to fly. When the vehicles first ran
with isolated parameter stores they would not arm, and the log said why in one
line: `PreArm: Motors: Check frame class and type`.

### How Gazebo runs three vehicles

The stock `iris_with_ardupilot` model hardcodes FDM port 9002, so including it
three times gives three vehicles fighting over one socket.
`scripts/make_swarm_world.py` clones the model per vehicle with the port SITL
actually uses for instance `i` - `9002 + 10i` - and writes
`worlds/swarm_runway.sdf` with them spaced 15 m apart. `launch_swarm.sh`
regenerates this on every Gazebo start, so the world always matches the vehicle
count.

In Gazebo mode the spacing lives in the world, not in `--custom-location`:
Gazebo owns the physics, so a model's pose *is* the vehicle's position, and a
custom home on top would double-count it.

## Flying the leader from the browser

The dashboard can fly the leader from the keyboard, with the followers holding
formation behind it. The whole demo is four clicks and a key:

1. **Arm + take off all (20 m)** — every vehicle, not just the leader. A
   follower left disarmed on the ground reports a large station error and looks
   like a formation failure, so there is no useful state where only the leader
   is up.
2. **Followers → FOLLOW** — writes the same `FOLL_*` set that
   `scripts/leader_follower.py` uses, so the button and the measured evaluation
   cannot drift apart.
3. **Take control** — puts the leader in `GUIDED`. Nothing moves until this is
   pressed; there are no live-by-default keys.
4. Hold **W / A / S / D** to fly north / west / south / east, **R / F** to
   climb and descend, **Q / E** to yaw, **shift** for 8 m/s instead of 3,
   **space** to stop.

Measured, three drones in Gazebo, hand-flown through the browser:

```
   take off all:  arm accepted, takeoff accepted  (all 3)
   formation:     d2 FOLLOW, d3 FOLLOW, station error 0.0 m
   hold W 20 s:   leader +93.1 m north, +0.0 m east, alt held 20.0 m
                  follower error 0.43 m and 0.36 m while hand-flown
   hold R  8 s:   leader 20.0 -> 30.4 m, followers tracked to 0.15 m vertically
   release keys:  groundspeed 4.9 -> 0.01 m/s within 1 s  (deadman)
   land all:      all three descended together and disarmed
```

Velocities are **NED**: `W` is north, not "forward". That matches
`FOLL_OFS_TYPE=0`, which is what the real aircraft must use while they have no
compass — the formation does not rotate with the leader's heading, so neither
does the operator's frame of reference.

### What stops the vehicle

Three independent things, which is the point:

| Event | What happens |
|---|---|
| Key released, or `space` | Zero velocity sent immediately |
| Tab hidden, window blurred | Zero velocity sent immediately |
| Browser stops talking for 0.5 s | Server commands zero (deadman) |
| Server dies | ArduPilot expires the `GUIDED` setpoint on its own (~3 s) |

Commands go out as `GUIDED` velocity setpoints, never as
`RC_CHANNELS_OVERRIDE`. An override fights a real transmitter for the same
channels; a mode-based setpoint is beaten the moment a safety pilot flips the
mode switch. Only one of those is the right default on real aircraft. The
server binds to loopback only, and `--no-control` removes the write path
entirely.

**A swarm that landed while still armed will refuse to take off again.** If the
leader lands while followers are in `FOLLOW`, they follow it down and stay
armed on the ground, and ArduPilot then rejects a `GUIDED` takeoff. The button
force-disarms anything armed at zero altitude first, and every arm and takeoff
waits for its `COMMAND_ACK` and reports the result on the page — the first
version of this failed silently, which is much worse. If it still refuses,
`./flyit --stop` and start again.

## Camera views

Each vehicle model carries a camera sensor on its own `gz-transport` topic
(`/drone{i}/camera`), generated by `scripts/make_swarm_world.py`. The dashboard
subscribes with the gz-transport Python bindings, JPEG-encodes with OpenCV, and
serves `multipart/x-mixed-replace` at `/api/video/{i}` — MJPEG, which for three
small feeds over loopback needs no signalling and nothing extra in the page.
Frames are dropped, never queued: a stalled viewer must not be able to make the
server accumulate memory or serve stale video.

### What the cameras cost

Less than expected, and the interesting part is why. Ten `real_time_factor`
samples each, three drones with the Gazebo GUI:

```
   cameras off (SWARM_CAMERAS=0):   mean RTF 0.656
   cameras on  (3 x 320x240 @10Hz): mean RTF 0.611
```

About 7 % relative. The low baseline is three SITL processes and the Gazebo
GUI, and it was already there before any camera existed.

Because the `ardupilot_gazebo` plugin is **lock-stepped**, a real-time factor
below 1.0 does not corrupt the simulated flight — SITL and Gazebo share one
clock, so the station-keeping numbers stay valid. What it costs is wall-clock
time. It also means the sensor's `10 Hz` is 10 Hz of *sim* time, which is why
the dashboard reported 6.3 fps at RTF 0.63 and 9.9 fps once the machine was
less loaded.

To measure it yourself:

```bash
SWARM_CAMERAS=0 flyit 3      # identical world, no camera sensors
gz topic -e -t /world/swarm_runway/stats -n 10 | grep real_time_factor
```

`SWARM_CAMERA_SIZE=640x480` and `SWARM_CAMERA_RATE=20` change the sensors;
`--no-video` stops the dashboard subscribing at all.

## Pick a target on the map

The formation map is clickable. One click converts the pixel to an absolute
latitude and longitude and sends the leader there; the followers hold formation
the whole way, because they are already a function of the leader.

```
click 120 m north, 60 m east of the leader
     5s   107.6 m to target   alt 30.0   followers 2.4 m, 1.6 m
    10s    57.8 m to target   alt 30.0   followers 1.6 m, 0.7 m
    15s     9.9 m to target   alt 30.0   followers 0.04 m, 0.65 m
    20s     0.1 m to target   arrived    followers 0.05 m, 0.07 m
```

The target is stored as an absolute position and re-projected on every poll, so
it stays put on the ground while the map itself stays leader-centred.

Targets go out as `SET_POSITION_TARGET_GLOBAL_INT`, whose latitude and longitude
are exact 1e7 integers. `MAV_CMD_DO_REPOSITION` is the obvious alternative and
carries them as float32 command parameters, which quantises a position to about
a metre here — visible as a formation that never quite settles.

## Mission: inspect an area, find the accident

Two surveys, both flown by the leader with the followers in formation:

| Button | What it flies |
|---|---|
| **Inspect the highway** | Out and back along the runway centreline, two lanes, 40–340 m north at 30 m |
| **Inspect the whole area** | Boustrophedon ("lawnmower") over 240 m × 160 m, three lanes at 35 m |
| **Abort** | Stops and holds position |

A survey plan contains exactly one vehicle no matter how many are flying. That
is the dividend of keeping the formation logic in the flight controller.

### There is something to find

The stock world is empty, so `scripts/make_swarm_world.py` stages an accident:
two cars and debris on the runway centreline, 250 m north, in saturated red and
yellow. Nothing else in the world is either colour — asphalt grey, markings
white, grass green, sky pale — which is what makes a hue threshold a legitimate
detector *here*. It is not a vehicle detector, and it has to be replaced
outright the day it points at real video.

`dashboard/detect.py` thresholds the frame, `server.py` projects the centroid
onto the ground from the drone's altitude and attitude, clusters nearby
sightings, and writes an annotated evidence frame to
`.run/latest/detections/<id>.jpg` — served in the page beside the geotag.

### Measured against the answer key

`worlds/scene.json` records where the accident was actually staged, so this is
testable rather than merely demonstrable:

```
highway survey, 30 m:   found at -35.3610019, 149.1649258   error 1.8 m
area survey,    35 m:   found at -35.3609936, 149.1649252   error 2.6 m
                        148 sightings from 2 drones, clustered to 1 report
negative control:       SWARM_ACCIDENT=0, same survey        0 detections
```

The negative control matters as much as the hit: over a clear road the detector
reported nothing at all, so it is keying on the wreck and not on asphalt,
markings or grass.

Two details carry most of the accuracy. The camera looks forward and down, so a
blob near the top of the frame is near the horizon where a small angular error
becomes an enormous ground error — the projection refuses to range anything
shallower than 12° below horizontal and returns a bearing instead. And each fix
is weighted by how much red it saw, so a close overhead look outweighs a distant
glimpse; that weighting alone is worth about 1.5 m.

```bash
SWARM_ACCIDENT=0 flyit 3          # negative control: nothing staged
SWARM_ACCIDENT_NORTH=400 flyit 3  # stage it somewhere else
```

### Point every camera at what you found

Once an accident is on the map, each detection card grows a **Point all cameras
here** button. Every drone then yaws so its own camera faces that ground
position, while the formation keeps tracking the leader — fly the leader
wherever you like and the swarm keeps staring at the wreck.

The cameras are bolted to the airframes (no gimbals, matching the real F450s),
so aiming a camera means yawing the aircraft. That is free here: with
`FOLL_OFS_TYPE=0` the formation offsets are in the NED frame and do not rotate
with heading, so yaw is an unused degree of freedom.

Measured, leader hand-flown north at 6 m/s with all three cameras aimed:

```
   camera off-axis: d1 +1.0  d2 +0.8  d3 +1.0   station 0.66 m, 0.73 m
   camera off-axis: d1 +1.6  d2 +1.5  d3 +0.7   station 1.24 m, 1.32 m
   camera off-axis: d1 +0.1  d2 +0.1  d3 +0.1   station 1.49 m, 1.58 m
```

And parked at a 45 m standoff, all three drones saw it at once:
`seen by drone 1, 3, 2`.

**Why each drone needs its own bearing.** ArduPilot's `FOLLOW` owns the yaw
controller — it *acks* `MAV_CMD_CONDITION_YAW` and then ignores it, and
`FOLL_YAW_BEHAVE` only offers "face the leader", "copy the leader" and "face the
direction of flight". Copying the leader's heading nearly works and fails where
it matters: close to the wreck the three drones wanted 0°, 16° and 344° — a 32°
spread, and closer still it exceeds what a 69° lens can cover. So while aiming,
the followers move to `GUIDED` and the dashboard holds their stations from the
ground, with their `FOLL_*` values left untouched so the hand-back is exact.

Two details carry it:

- **The aim rides inside the command that is already moving the vehicle.** A
  separate yaw command gets reset to "face the direction of travel" by the very
  next position target, so the camera swings away exactly while the vehicle
  moves.
- **Follower targets carry the leader's velocity as feed-forward.** Without it a
  follower flies *to* a point and decelerates into it, leaving the formation
  about **25 m** behind a leader doing 10 m/s. With it, 1.0–1.6 m.

The trade is dependency, not accuracy: while aiming, the formation exists
because the ground station is talking. If the dashboard stops, the followers
hold their last commanded position — safe, but no longer following. That is why
aiming is a temporary mode with an explicit hand-back rather than the default.

## What it needs

Already present on this machine at `/home/ttz/workspaces/SSS_2026`:

| Thing | Where | Note |
|---|---|---|
| ArduPilot source + built SITL | `SSS_2026/ardupilot` | 4.6.0-dev. Aircraft run 4.7.0 — see caveats |
| `ardupilot_gazebo` plugin | `SSS_2026/ardupilot_gazebo` | already compiled |
| Gazebo | system `gz` | Harmonic 8.10 / Garden 7.9 |
| MAVProxy + pymavlink | `~/.local/bin` | 2.4.49 |
| Mission Planner | `SSS_2026/MissionPlanner` | runs under mono |

Override paths by exporting `SSS_ROOT` or `ARDUPILOT` before running anything.

## Step 1 — two vehicles with distinct system IDs

```bash
./scripts/launch_swarm.sh 2          # leave this running
python3 scripts/check_vehicles.py 2  # in another terminal
```

Expected:

```
  vehicle 0  port 14551  sysid 1  STABILIZE  armed=False  -35.363262,149.165237 alt 0.0m
  vehicle 1  port 14561  sysid 2  STABILIZE  armed=False  -35.363262,149.165457 alt 0.0m
  vehicle 0  port 14551  ok - one vehicle [1]
  vehicle 1  port 14561  ok - one vehicle [2]
RESULT: PASS - 2 vehicles up with distinct system IDs [1, 2]
```

Distinct system IDs are not cosmetic: `FOLL_SYSID` is how a follower picks its
leader out of the traffic, so two vehicles claiming sysid 1 makes the whole
pattern impossible.

The spawn points are 20 m apart (`SPACING_M` to change), because vehicles that
start stacked on one point make offset errors impossible to read.

Ports per vehicle `i`:

| Port | Owner |
|---|---|
| `14550 + 10i` | `relay.py` |
| `14551 + 10i` | test scripts |
| `14552 + 10i` | `dashboard/server.py` |
| `14553 + 10i` | Mission Planner / QGroundControl |
| `14554 + 10i` | `scripts/flight_log.py` |

Two endpoints because a UDP port has exactly one owner — the relay and a GCS
cannot share one. On the aircraft, `mavlink-router` fans out the same way.

## Step 2 — carry the leader's position to the follower

```bash
python3 companion/swarm_agent/relay.py        # leave running
```

Expected:

```
  leader on 14550 is sysid 1
  follower on 14560 is sysid 2
Relaying ['GLOBAL_POSITION_INT'] from sysid 1 to 1 follower(s) at up to 10 Hz
```

This is the piece that also ships to the real Raspberry Pi. In simulation it
bridges UDP endpoints; on the aircraft the same loop reads the leader's
position off Wi-Fi and writes it into the local flight controller's UART.
Raw message bytes are forwarded untouched so the leader's system ID survives —
a rewritten sysid would stop matching `FOLL_SYSID`.

## Step 3 — fly the formation and measure it

```bash
python3 scripts/leader_follower.py --kill-relay-at 48
```

This arms both vehicles, climbs to 20 m, puts vehicle 2 in `FOLLOW`, flies the
leader 120 m north, then kills the relay partway to prove it matters.

Measured output:

```
  commanded offset: +20m north, +0m east of the leader
      0             0m        +0.0m N   +20.0m E     28.3m
      8            38m        +0.9m N    +4.9m E     19.7m
     20           121m       +22.7m N    +0.6m E      2.7m
     32           120m       +20.0m N    +0.0m E      0.0m
  ---- relay stopped ----
     56           154m       -14.4m N    +0.1m E     34.4m   (no relay)
     68           241m      -100.6m N    +0.0m E    120.6m   (no relay)

  average error while relaying:             0.0m
  worst error after the relay stopped:      120.6m
RESULT: PASS - follower held station within 6m.
```

The second half is the part worth keeping. When the relay stops, the follower
freezes where it is and the leader flies on alone. That is the proof the
formation is actually built on the relayed position and not on some other path
inside the simulation — and it is also what leader-loss looks like, which is
still an open decision (hold, return, or land).

## Tear down

```bash
./scripts/stop_swarm.sh    # prints "All simulation processes stopped."
```

Worth using rather than Ctrl-C. A leftover SITL instance binds the same ports,
so the next test commands one vehicle while reading position from another —
a failure that looks exactly like broken `FOLLOW`. `check_vehicles.py` detects
this and refuses to pass, but stopping cleanly avoids it entirely.

## Caveats on what the numbers mean

**gz-transport picks a network interface, and can pick a dead one.** Camera
topics stayed advertised while no subscriber ever received a frame, with
`Exception sending a multicast message: Network is unreachable` buried in the
Gazebo log — a down `eno1` and `docker0` were enough to cause it. Everything
here runs on one machine, so `scripts/env.sh` pins `GZ_IP=127.0.0.1` and the
simulation no longer depends on the host's LAN being healthy.

**Detection needs the wreck to be big enough in frame.** At a 90 m standoff from
40 m up, the cars subtend about 10 px and fall under the area threshold — the
cameras are aimed correctly and report nothing. Inside roughly 60 m of slant
range they detect reliably. That is a property of a 320×240 sensor and a 69°
lens, not a bug, but it sets how close a survey has to pass.

**`LOITER` is not a hold — it lands.** Every early survey ended with the swarm
on the ground, with no failsafe and nothing in the flight log but a mode change.
`LOITER` holds position horizontally but takes its **climb rate from the
throttle stick**, and SITL's RC throttle reads 1000 µs — full descent. Every
"hold here" was quietly commanding a landing. Holding the current position as a
`GUIDED` target needs no RC input at all, and position targets (unlike velocity
targets) do not expire, so one message does it. Worth remembering before any of
this points at an aircraft whose transmitter is switched off.

**Only one thing may command the leader at a time.** Manual keys, a map click
and a survey all send setpoints several times a second. Two at once do not
blend — they alternate, and the vehicle does neither. Every entry point claims
sole ownership first; without it, a take-off would stop short at the altitude of
some forgotten target.

**0.0 m error is not a real result.** SITL has no GPS noise by default, so both
vehicles share a perfect common position truth. Real aircraft carry independent
non-RTK M10 receivers whose *relative* error is metres — which is the binding
constraint on formation spacing described in the architecture doc. Until noise
is modelled, this rig proves the plumbing works, not that 20 m spacing is safe.

**The simulated airframe is not an F450.** The Gazebo model is the stock iris.
`config/sitl/f450.parm` sets the frame class and type correctly, but the mass,
inertia and thrust curve are still the iris's. Fine for the plumbing; not fine
for tuning `FOLL_POS_P` and expecting it to transfer to a 450 mm frame.

**Firmware versions differ.** SITL here is 4.6.0-dev; the aircraft run 4.7.0.
`FOLL_*` parameters were checked against this tree and match, but a parameter
sweep is worth redoing against 4.7.0 before trusting values on hardware.

**Each vehicle needs its own parameter store.** `sim_vehicle.py` only creates
per-instance directories when a single invocation spawns several vehicles. Run
as separate invocations - which is what gives each vehicle its own ports - every
instance shares one `eeprom.bin`, so setting a follower's `FOLL_*` values also
writes them onto the leader, and the leader then tries to follow itself.
`launch_swarm.sh` passes `--use-dir .run/v$i` to isolate them; the evaluation
also sets the leader's `FOLL_ENABLE=0` explicitly rather than trusting it.

**No compass on the real aircraft** (`COMPASS_DEV_ID=0`), so
`config/follow/trail_20m.param` uses `FOLL_OFS_TYPE=0` (North-East-Down). The
formation holds a compass-aligned offset and does **not** rotate as the leader
turns.

## Not built yet

- An F450 Gazebo model — geometry, mass and thrust curve to match the real frame
- Mission Planner against both vehicles at once
- GPS noise (`SIM_GPS*`) to make the offset error realistic
- Link degradation runs: `relay.py --loss 0.3 --delay 0.2` works but has no
  measured baseline yet
- WebRTC instead of MJPEG — the right answer for video off a real aircraft
  over Wi-Fi, unnecessary for three feeds over loopback
- An F450-accurate camera (the sim sensor is not the Pi Camera v2's field of
  view or resolution, which matters the moment the video feeds vision code)
