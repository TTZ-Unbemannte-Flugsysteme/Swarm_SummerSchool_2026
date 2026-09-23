# Simulation Testing

Before flying real hardware, you must test your algorithms in a simulated environment to prevent crashes and verify logic safely.

The simulation is three ArduPilot drones flying a leader-follower formation in Gazebo, with a browser that flies the leader, streams a camera from each drone, and runs the inspection surveys.

## Task 1 — Get the simulation running

This first task exists only to show you how the simulation works. Nothing here
is a swarm of your own yet: you are checking that the tooling runs on your machine.

The simulation code lives on the **`simulation`** branch of this repository, separate from `main` (which holds this documentation).

[Browse the `simulation` branch](https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026/tree/simulation){ .md-button .md-button--primary }

Clone just that branch into its own folder:

```bash
git clone -b simulation --single-branch \
  https://github.com/TTZ-Unbemannte-Flugsysteme/Swarm_SummerSchool_2026.git swarm-simulation
cd swarm-simulation
```

If you already have the repository checked out, fetch the branch and switch to it:

```bash
git fetch origin simulation
git switch simulation
```

The branch is under active development — run `git pull` at the start of each
session to pick up the latest changes.

## Setup

Everything the simulation needs — Gazebo, ArduPilot SITL, the `ardupilot_gazebo` plugin and the Python packages — is installed by one script:

```bash
./install.sh
```

Budget **30–40 minutes**, nearly all of it the ArduPilot compile running unattended. It is safe to re-run: every step checks for its own result first, so a second run after a failure resumes rather than starting over.

!!! tip "If you already have ArduPilot built"
    Point at the directory that *contains* your `ardupilot` checkout and skip the compile entirely:
    ```bash
    export SSS_ROOT=/path/to/parent
    ./install.sh --no-apt
    ```

Requires Ubuntu 22.04 or 24.04 and about 10 GB of free disk.

## Run It

Open a **new terminal** — the installer put `flyit` on your `PATH`, and the shell you installed from does not know that yet — then:

```bash
flyit --check   # every dependency, ending in RESULT: PASS or FAIL
flyit           # 3 drones in Gazebo + the operator dashboard
```

The dashboard opens at <http://127.0.0.1:8760>. Wait about a minute before expecting the buttons to work: GPS and the EKF need to settle, and the buttons enable themselves once they have.

```bash
flyit 2            # two drones instead of three
flyit --no-gazebo  # headless physics, starts in seconds, no 3D window
flyit --stop       # shut it all down
```

!!! warning "Always stop with `flyit --stop`"
    Closing the windows is not enough — the simulator keeps running, and a leftover run holds the network ports the next one needs.

## Full Walkthrough

The page below is the whole path from a bare laptop to three drones flying, with copy buttons on every command and a troubleshooting table.

[Open it in its own tab](quickstart.html){ .md-button } &nbsp; [Teaching version, with slides](workshop.html){ .md-button } &nbsp; [Design rationale](architecture.html){ .md-button }

<iframe src="../quickstart.html" width="100%" height="900px" style="border: 1px solid rgba(128,128,128,.3); border-radius: 8px; margin-top: 20px;"></iframe>

## Task 2 — A decentralised swarm

Task 1 gives you one leader and followers that depend on it. This task asks the
opposite question: how does a group agree on something with **no central
coordinator**?

Start from the consensus material below and use it as inspiration — a starting
point to argue with, not a specification to implement.

[:material-file-powerpoint: Download: Multi-Agent Systems — Consensus (PPTX, 62 MB)](../assets/Multi-Agent-Systems-Consensus.pptx){ .md-button }

!!! note
    Where P2P ("no central relay") genuinely wins is if you need the swarm to
    work with no ground infrastructure at all. If the ground station is there
    anyway — and Mission Planner is in your architecture — that argument mostly
    evaporates.

## Task 3 — Leader-follower in the real world (Friday)

Team up with another group and make leader-follower **testable on real
hardware**. Friday is the flight day.

Flying is the last step, not the first. Before anything leaves the ground:

1. **Write the test cases first.** Decide what you are measuring and what counts
   as pass or fail *before* you fly — station-keeping error, time to recover
   after the leader turns, what happens when the leader's stream stops.
2. **Run every case in simulation and record the result.** A case you cannot
   pass in Gazebo will not pass in the air. `scripts/leader_follower.py` below is
   the pattern to copy: it measures, and it exits non-zero when the formation
   does not hold.
3. **Agree the split with the other group** — who flies the leader, who flies
   the follower, who watches telemetry, and who calls an abort.
4. **Decide the leader-loss behaviour.** If the follower stops hearing the
   leader, should it hold, return, or land? Decide that on the ground rather
   than discovering the default in the air.

The [Verification Checklist](#verification-checklist) at the bottom of this page
is the minimum bar, not your whole test plan.

## Running Algorithms

1. **Connect your algorithm to the simulator**: each vehicle `i` exposes its own MAVLink endpoints on `127.0.0.1`, so two consumers never fight over one port. Vehicle 0 is on `14550`–`14554`, vehicle 1 on `14560`–`14564`, and so on. Point your script at the *control* endpoint, `14551 + 10i`.
2. **Takeoff sequence**: test basic commands like arming, taking off, and holding a hover.
3. **Complex logic**: execute your specific swarm or navigation logic. Monitor the console output and the drone's behaviour in Gazebo.

The repository ships a scripted evaluation you can copy as a starting point. It flies the formation, measures each follower's station-keeping error and exits non-zero if the formation does not hold:

```bash
python3 scripts/leader_follower.py --vehicles 3
```

```
  follower1: closest 0.0m, settled 0.0m (tolerance 6m)
  follower2: closest 0.0m, settled 0.0m (tolerance 6m)
RESULT: PASS - every follower held station within 6m.
```

Every run writes its own log directory. `.run/latest/flight_log.txt` is the merged log of all drones — modes, arming and every autopilot message — and answers most questions about why a drone did or did not do something.

## Verification Checklist

Before moving to real hardware, ensure:

- [ ] `flyit --check` reports `RESULT: PASS`.
- [ ] The algorithm does not send conflicting commands.
- [ ] Emergency stop / Land commands work reliably.
- [ ] The drone correctly handles losing communication (failsafe testing).

!!! danger "A clean simulation result is not a safe flight"
    SITL has no GPS noise by default, so every vehicle shares one perfect position truth and formation error reads 0.0 m. Real aircraft carry independent receivers whose *relative* error is metres, which is what actually constrains how close drones may fly. The simulation proves your logic, not your spacing — see the [design rationale](architecture.html) for what else does not transfer.
