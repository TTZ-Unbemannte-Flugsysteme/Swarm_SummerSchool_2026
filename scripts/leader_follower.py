#!/usr/bin/env python3
"""Fly a leader-follower formation in SITL and measure whether it holds.

Run ./launch_swarm.sh first, then in another terminal:

    python3 companion/swarm_agent/relay.py --leader 14550 --followers 14560

then here:

    python3 scripts/leader_follower.py

The test arms both vehicles, takes them to altitude, puts vehicle 2 into
FOLLOW, flies the leader north, and then reports the follower's actual offset
from the leader against the commanded one. It exits non-zero if the formation
never converges, so it works as a regression check.
"""
import argparse
import math
import os
import signal
import sys
import time

from pymavlink import mavutil

CONTROL = lambda i: 14551 + 10 * i
TAKEOFF_ALT = 20.0
FOLLOW_MODE = 23

# Shared by every follower; the per-vehicle station is added below.
BASE_PARAMS = {
    "FOLL_ENABLE": 1,
    "FOLL_SYSID": 1,
    "FOLL_OFS_TYPE": 0,      # North-East-Down: no compass on the real aircraft
    "FOLL_ALT_TYPE": 1,
    "FOLL_YAW_BEHAVE": 0,
    "FOLL_DIST_MAX": 1000,
    "FOLL_POS_P": 0.1,
    "FOLL_TIMEOUT": 3.0,
}

# Station for follower k (0-based) as (north, east) metres from the leader.
# A trailing V, spaced well clear of GNSS relative error - see the architecture
# doc on why single-digit spacing is not safe on the real aircraft.
STATIONS = [(-25.0, -20.0), (-25.0, 20.0), (-50.0, 0.0), (-50.0, -40.0)]


def station_for(k):
    return STATIONS[k % len(STATIONS)]


def ned_offset(ref, pos):
    """Metres north/east from ref to pos, both (lat, lon) in degrees."""
    dn = (pos[0] - ref[0]) * 111320.0
    de = (pos[1] - ref[1]) * 111320.0 * math.cos(math.radians(ref[0]))
    return dn, de


class Vehicle:
    def __init__(self, index, name):
        self.index = index
        self.name = name
        port = CONTROL(index)
        self.conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
        hb = self.conn.wait_heartbeat(timeout=20)
        if hb is None:
            raise SystemExit(f"FAIL: no heartbeat from {name} on udp:{port}")
        self.sysid = self.conn.target_system
        print(f"  {name:<9} udp:{port}  sysid {self.sysid}")

    def set_param(self, name, value):
        self.conn.mav.param_set_send(
            self.conn.target_system, self.conn.target_component,
            name.encode(), float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    def get_param(self, name, timeout=5):
        self.conn.mav.param_request_read_send(
            self.conn.target_system, self.conn.target_component, name.encode(), -1)
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if m and m.param_id.strip("\x00") == name:
                return m.param_value
        return None

    def set_message_rate(self, msg_id, hz):
        """Ask the FC to stream a message faster than the GCS default."""
        self.conn.mav.command_long_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
            msg_id, int(1e6 / hz), 0, 0, 0, 0, 0)

    def set_mode(self, mode_name_or_id):
        if isinstance(mode_name_or_id, str):
            mode_id = self.conn.mode_mapping()[mode_name_or_id]
        else:
            mode_id = mode_name_or_id
        self.conn.mav.set_mode_send(
            self.conn.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id)

    def wait_mode(self, mode_id, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self.conn.recv_match(type="HEARTBEAT", blocking=True, timeout=2)
            if m and m.custom_mode == mode_id:
                return True
        return False

    def arm(self, timeout=90):
        """Keep asking until the autopilot agrees, and remember why it did not.

        90 seconds, not 30. Several pre-arm checks are transient on a fresh
        SITL: the EKF settles, and the simulated throttle stick starts at
        minimum and only reaches neutral about a minute in - until it does,
        arming in GUIDED is refused with "Throttle (RC3) is not neutral".
        A 30-second window caught the EKF but not that one, so a run started
        promptly after launch would fail while the identical run a few minutes
        later passed.
        """
        self.prearm_text = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.conn.mav.command_long_send(
                self.conn.target_system, self.conn.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 1, 0, 0, 0, 0, 0, 0)
            m = self.conn.recv_match(type=["HEARTBEAT", "STATUSTEXT"],
                                     blocking=True, timeout=2)
            if m is None:
                continue
            if m.get_type() == "STATUSTEXT":
                if m.severity <= 4:
                    self.prearm_text = m.text.strip()
                continue
            if m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                return True
        return False

    def last_prearm_text(self):
        return getattr(self, "prearm_text", None)

    def takeoff(self, alt):
        self.conn.mav.command_long_send(
            self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, 0, alt)

    def goto(self, lat, lon, alt):
        self.conn.mav.set_position_target_global_int_send(
            0, self.conn.target_system, self.conn.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,
            int(lat * 1e7), int(lon * 1e7), alt,
            0, 0, 0, 0, 0, 0, 0, 0)

    def position(self, timeout=5):
        """Most recent position, not the oldest queued one.

        At 10 Hz with seconds between samples the socket builds a backlog, and
        a blocking recv_match hands back the front of that queue. Comparing a
        stale leader position against a fresh follower position invents offset
        errors of tens of metres while the leader is moving fast.
        """
        latest = None
        while True:
            m = self.conn.recv_match(type="GLOBAL_POSITION_INT", blocking=False)
            if m is None:
                break
            latest = m
        if latest is None:
            latest = self.conn.recv_match(
                type="GLOBAL_POSITION_INT", blocking=True, timeout=timeout)
        if latest is None:
            return None
        return (latest.lat / 1e7, latest.lon / 1e7, latest.relative_alt / 1000.0)

    def wait_alt(self, target, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            p = self.position()
            if p and p[2] >= target * 0.9:
                return True
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=3,
                    help="how many vehicles are running (1 leader + the rest)")
    ap.add_argument("--tolerance", type=float, default=6.0,
                    help="metres of offset error to call the formation held")
    ap.add_argument("--settle", type=float, default=60.0,
                    help="seconds to watch the formation after the leader moves")
    ap.add_argument("--kill-relay-at", type=float, default=0.0,
                    help="stop the relay this many seconds in and keep watching, "
                         "to prove the relay is what holds the formation together")
    args = ap.parse_args()

    print("Connecting to vehicles:")
    leader = Vehicle(0, "leader")
    followers = [Vehicle(i, f"follower{i}") for i in range(1, args.vehicles)]
    if not followers:
        print("FAIL: need at least 2 vehicles for leader-follower")
        return 1

    # An unset leader can inherit FOLL_ENABLE=1 from a previous run and try to
    # follow itself. Switch it off rather than assuming it is off.
    print("\nDisabling FOLLOW on the leader:")
    leader.set_param("FOLL_ENABLE", 0)
    time.sleep(1.5)
    got = leader.get_param("FOLL_ENABLE")
    print(f"  leader FOLL_ENABLE = {got}  " + ("ok" if got == 0 else "STILL ON"))
    if got != 0:
        print("\nFAIL: the leader would try to follow itself.")
        return 1

    print("\nConfiguring followers:")
    stations = {}
    for k, f in enumerate(followers):
        n, e = station_for(k)
        stations[f.name] = (n, e)
        params = dict(BASE_PARAMS)
        params.update({"FOLL_OFS_X": n, "FOLL_OFS_Y": e, "FOLL_OFS_Z": 0.0})
        for name, value in params.items():
            f.set_param(name, value)
        time.sleep(1.5)
        # Read back the values that decide the whole behaviour.
        ok = True
        for name in ("FOLL_ENABLE", "FOLL_SYSID", "FOLL_OFS_X", "FOLL_OFS_Y"):
            got = f.get_param(name)
            want = params[name]
            if got is None or abs(got - want) > 0.01:
                print(f"  {f.name}: {name} = {got}, wanted {want}  MISMATCH")
                ok = False
        if not ok:
            print(f"\nFAIL: {f.name} did not accept its FOLLOW parameters.")
            return 1
        print(f"  {f.name} (sysid {f.sysid}): station {n:+.0f}m N {e:+.0f}m E  ok")

    print("\nRaising the leader's position stream to 10 Hz "
          "(GCS default is ~4 Hz, too slow for station keeping)")
    leader.set_message_rate(mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 10)

    print("\nArming and taking off:")
    for v in [leader] + followers:
        v.set_mode("GUIDED")
        if not v.wait_mode(v.conn.mode_mapping()["GUIDED"]):
            print(f"  FAIL: {v.name} would not enter GUIDED")
            return 1
        if not v.arm():
            # The autopilot always says why; guessing wastes far more time
            # than reading it. A common one here is "Throttle (RC3) is not
            # neutral": SITL's simulated throttle stick sits at minimum, and
            # arming in GUIDED wants it centred. That only bites when the
            # vehicle is *already* in GUIDED - typically because an earlier
            # attempt failed and left it there - so say so explicitly.
            why = v.last_prearm_text() if hasattr(v, "last_prearm_text") else None
            print(f"  FAIL: {v.name} would not arm"
                  + (f" - {why}" if why else " (EKF or GPS not ready yet?)"))
            print(f"        check .run/latest/flight_log.txt for the reason,")
            print(f"        and note a failed run can leave a vehicle in GUIDED;")
            print(f"        ./flyit --stop and start again clears that.")
            return 1
        v.takeoff(TAKEOFF_ALT)
        print(f"  {v.name} armed, climbing to {TAKEOFF_ALT:.0f}m")

    for v in [leader] + followers:
        if not v.wait_alt(TAKEOFF_ALT):
            print(f"  FAIL: {v.name} never reached altitude")
            return 1
    print("  both at altitude")

    print("\nPutting followers into FOLLOW mode")
    for f in followers:
        f.set_mode(FOLLOW_MODE)
        if not f.wait_mode(FOLLOW_MODE):
            print(f"  FAIL: {f.name} would not enter FOLLOW")
            return 1
        print(f"  {f.name} is in FOLLOW")

    lpos = leader.position()
    target_lat = lpos[0] + 120.0 / 111320.0        # 120 m north
    print(f"\nSending the leader 120m north, then watching the formation for "
          f"{args.settle:.0f}s")
    leader.goto(target_lat, lpos[1], TAKEOFF_ALT)

    hdr = "  ".join(f"{f.name}".rjust(16) for f in followers)
    print(f"\n  stations: " + ", ".join(
        f"{f.name} {stations[f.name][0]:+.0f}N {stations[f.name][1]:+.0f}E"
        for f in followers))
    print(f"\n  {'t':>5}  {'leader':>8}  {hdr}")

    start = time.time()
    errors = {f.name: [] for f in followers}
    post_kill = {f.name: [] for f in followers}
    best = {f.name: None for f in followers}
    killed = False

    while True:
        elapsed = time.time() - start
        if elapsed >= args.settle:
            break

        if args.kill_relay_at and not killed and elapsed >= args.kill_relay_at:
            # By PID, from the file relay.py writes. A pattern kill here would
            # also match the shell that started this test.
            pidfile = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", ".run", "relay.pid")
            try:
                with open(pidfile) as fh:
                    os.kill(int(fh.read().strip()), signal.SIGTERM)
            except (OSError, ValueError) as exc:
                print(f"  (could not stop the relay via {pidfile}: {exc})")
            killed = True
            print("  ---- relay stopped: the leader's position no longer "
                  "reaches the followers ----")
            # Send the leader somewhere new. A follower that still tracks it is
            # getting position from somewhere other than the relay.
            leader.goto(lpos[0] + 240.0 / 111320.0, lpos[1], TAKEOFF_ALT)

        lp = leader.position()
        if not lp:
            continue
        moved_n, _ = ned_offset((lpos[0], lpos[1]), (lp[0], lp[1]))
        cells = []
        for f in followers:
            fp = f.position()
            if not fp:
                cells.append("     no data")
                continue
            want_n, want_e = stations[f.name]
            dn, de = ned_offset((lp[0], lp[1]), (fp[0], fp[1]))
            err = math.hypot(dn - want_n, de - want_e)
            if killed:
                post_kill[f.name].append(err)
            else:
                errors[f.name].append(err)
                b = best[f.name]
                best[f.name] = err if b is None else min(b, err)
            cells.append(f"{err:9.1f}m err")
        print(f"  {elapsed:5.0f}  {moved_n:7.0f}m  " + "  ".join(c.rjust(16) for c in cells)
              + ("   (no relay)" if killed else ""))
        time.sleep(4)

    print()
    if not any(errors.values()):
        print("RESULT: FAIL - no position data")
        return 1

    all_ok = True
    for f in followers:
        e = errors[f.name]
        if not e:
            print(f"  {f.name}: no data")
            all_ok = False
            continue
        final = sum(e[-3:]) / len(e[-3:])
        print(f"  {f.name}: closest {best[f.name]:.1f}m, "
              f"settled {final:.1f}m (tolerance {args.tolerance:.0f}m)")
        if final > args.tolerance:
            all_ok = False

    if killed:
        worst = max((max(v) for v in post_kill.values() if v), default=0.0)
        base = max((sum(errors[f.name][-3:]) / max(1, len(errors[f.name][-3:]))
                    for f in followers if errors[f.name]), default=0.0)
        print(f"\n  worst error after the relay stopped: {worst:.1f}m")
        if worst > max(base * 3, 15.0):
            print("  Negative control PASSED: the formation fell apart without the")
            print("  relay, which proves the relay is delivering the leader's")
            print("  position - not some other path in the simulation.")
        else:
            print("  Negative control INCONCLUSIVE: followers held station with the")
            print("  relay stopped. Check for a stray MAVProxy forwarding rule.")

    print()
    if all_ok:
        print(f"RESULT: PASS - every follower held station within "
              f"{args.tolerance:.0f}m. Leader-follower works end to end.")
        return 0
    print("RESULT: FAIL - at least one follower never reached its station.")
    print("        If errors never shrank, the leader's position is not reaching")
    print("        the followers - check relay.py is running.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
