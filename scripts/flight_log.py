#!/usr/bin/env python3
"""Unified flight log for the whole swarm, one line per event.

The autopilot's own messages (arming checks, EKF state, mode changes, failsafes)
are what you actually need when a flight misbehaves, and reading three separate
MAVProxy consoles to correlate them is painful. This merges every vehicle's
stream into one timestamped, colour-coded log.

    python3 scripts/flight_log.py --vehicles 3

Reads the flight-log endpoints (14554 + 10i), so it does not compete with the
relay, the test scripts, the dashboard, or a GCS.

Output goes to the terminal AND to a per-session file, so a later run never
overwrites an earlier one:

    .run/sessions/<YYYYmmdd-HHMMSS>/flight_log.txt
    .run/latest/flight_log.txt        <- symlink to the newest session

Each line carries both wall-clock time and seconds since the log started, so
events can be lined up against the dataflash .BIN logs SITL writes to
.run/v<i>/logs/.
"""
import argparse
import datetime
import os
import re
import sys
import threading
import time
from queue import Queue

from pymavlink import mavutil

# Per-vehicle colour, matching the dashboard's series order.
COLORS = ["\033[38;5;208m", "\033[38;5;33m", "\033[38;5;35m",
          "\033[38;5;178m", "\033[38;5;170m"]
SEV = {
    0: ("\033[1;41m", "EMERGENCY"), 1: ("\033[1;41m", "ALERT"),
    2: ("\033[1;31m", "CRIT"),      3: ("\033[31m", "ERROR"),
    4: ("\033[33m", "WARN"),        5: ("\033[0m", "NOTICE"),
    6: ("\033[0m", "INFO"),         7: ("\033[2m", "DEBUG"),
}
RESET = "\033[0m"
DIM = "\033[2m"

events = Queue()


def watch(index, port, use_color):
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
    tag = COLORS[index % len(COLORS)] if use_color else ""
    sysid = None
    last_mode = None
    last_armed = None

    while True:
        msg = conn.recv_match(
            type=["STATUSTEXT", "HEARTBEAT", "EKF_STATUS_REPORT"],
            blocking=True, timeout=5)
        if msg is None:
            continue
        if sysid is None:
            sysid = msg.get_srcSystem()
        who = f"{tag}drone {sysid}{RESET if use_color else ''}"
        t = msg.get_type()

        if t == "STATUSTEXT":
            color, label = SEV.get(msg.severity, ("", "INFO"))
            text = msg.text.strip()
            if not use_color:
                color = ""
            events.put((time.time(), f"{who}  {color}{label:<9}{RESET if use_color else ''} {text}"))

        elif t == "HEARTBEAT":
            mode = mavutil.mode_mapping_acm.get(msg.custom_mode, f"mode {msg.custom_mode}")
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if last_mode is not None and mode != last_mode:
                events.put((time.time(), f"{who}  MODE      {last_mode} -> {mode}"))
            if last_armed is not None and armed != last_armed:
                events.put((time.time(),
                            f"{who}  {'ARMED' if armed else 'DISARMED':<9} "
                            f"in {mode}"))
            last_mode, last_armed = mode, armed


ANSI = re.compile(r"\033\[[0-9;]*m")


def session_paths(explicit):
    """Return (session_dir, logfile) for this run, creating what is needed."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = os.path.join(repo, ".run")
    if explicit:
        sdir = explicit
    else:
        # Share flyit's session when it set one; otherwise start our own.
        sid = os.environ.get("SWARM_SESSION") or \
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        sdir = os.path.join(run, "sessions", sid)
    os.makedirs(sdir, exist_ok=True)
    link = os.path.join(run, "latest")
    try:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(os.path.relpath(sdir, run), link)
    except OSError:
        pass  # a symlink is a convenience, never a reason to fail
    return sdir, os.path.join(sdir, "flight_log.txt")


def watch_dataflash(vehicles, repo):
    """Announce each dataflash .BIN as SITL opens it.

    The .BIN files live in shared per-vehicle directories, so "the newest ones
    belong to this session" is a guess. Naming them in the log, timestamped,
    makes the link exact - and ArduPilot only opens one once a vehicle arms,
    so their absence is itself informative.
    """
    def listing(i):
        d = os.path.join(repo, ".run", f"v{i}", "logs")
        try:
            return {f for f in os.listdir(d) if f.endswith(".BIN")}
        except OSError:
            return set()

    known = {i: listing(i) for i in range(vehicles)}
    while True:
        time.sleep(5)
        for i in range(vehicles):
            now = listing(i)
            for new in sorted(now - known[i]):
                events.put((time.time(),
                            f"drone {i + 1}  DATAFLASH .run/v{i}/logs/{new}"))
            known[i] = now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=3)
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--session-dir", default=None,
                    help="where to write flight_log.txt (default: this run's session)")
    args = ap.parse_args()
    use_color = not args.no_color and sys.stdout.isatty()

    sdir, logpath = session_paths(args.session_dir)
    ports = [14554 + 10 * i for i in range(args.vehicles)]
    started = datetime.datetime.now()

    header = [
        f"Flight log - session {os.path.basename(sdir)}",
        f"started    {started.isoformat(timespec='seconds')}",
        f"vehicles   {args.vehicles} on ports {ports}",
        f"this file  {logpath}",
        f"dataflash  .run/v*/logs/*.BIN (this session's are the newest)",
        "",
    ]
    logfile = open(logpath, "w", buffering=1)
    for line in header:
        print(line)
        logfile.write(line + "\n")

    for i, port in enumerate(ports):
        threading.Thread(target=watch, args=(i, port, use_color), daemon=True).start()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    threading.Thread(target=watch_dataflash, args=(args.vehicles, repo),
                     daemon=True).start()

    t0 = time.time()
    try:
        while True:
            when, line = events.get()
            clock = datetime.datetime.fromtimestamp(when).strftime("%H:%M:%S")
            elapsed = f"{when - t0:7.1f}s"
            stamp = f"{clock} {elapsed}"
            print(f"{DIM if use_color else ''}{stamp}{RESET if use_color else ''} {line}",
                  flush=True)
            # The file gets the same line without the escape codes.
            logfile.write(ANSI.sub("", f"{stamp} {line}") + "\n")
    except KeyboardInterrupt:
        msg = f"\nFlight log stopped at {datetime.datetime.now().isoformat(timespec='seconds')}"
        print(msg)
        logfile.write(msg + "\n")
        logfile.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
