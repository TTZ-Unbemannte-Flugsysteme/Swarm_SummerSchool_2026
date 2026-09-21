#!/usr/bin/env python3
"""Verify that N SITL vehicles are up, each with a distinct system ID.

    python3 check_vehicles.py [count]

Prints one line per vehicle and exits non-zero if any check fails, so it can
be used as a gate in a longer script.
"""
import sys
import time

from pymavlink import mavutil

TIMEOUT = 15


def sysids_on(port, listen=3.0):
    """Every distinct system ID heard on one endpoint.

    More than one means duplicate SITL instances are bound to the same port.
    That silently ruins every later test - commands go to one vehicle while
    position comes back from another - so it is worth catching up front.
    """
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
    seen = set()
    deadline = time.time() + listen
    while time.time() < deadline:
        m = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        if m is not None:
            seen.add(m.get_srcSystem())
    conn.close()
    return seen


def probe(port):
    """Return (sysid, heartbeat, gps) for the vehicle on this UDP port."""
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
    hb = conn.wait_heartbeat(timeout=TIMEOUT)
    if hb is None:
        return None
    # Ask for a position fix so we can confirm the EKF is actually running.
    gps = conn.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=TIMEOUT)
    info = {
        "sysid": conn.target_system,
        "mode": mavutil.mode_string_v10(hb),
        "armed": bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED),
        "lat": gps.lat / 1e7 if gps else None,
        "lon": gps.lon / 1e7 if gps else None,
        "alt_m": gps.relative_alt / 1000.0 if gps else None,
    }
    conn.close()
    return info


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    ports = [14551 + 10 * i for i in range(count)]  # control ports

    print(f"Probing {count} vehicle(s)...\n")
    results = {}
    for i, port in enumerate(ports):
        info = probe(port)
        if info is None:
            print(f"  vehicle {i}  port {port}  FAIL - no heartbeat in {TIMEOUT}s")
            continue
        results[port] = info
        pos = (
            f"{info['lat']:.6f},{info['lon']:.6f} alt {info['alt_m']:.1f}m"
            if info["lat"] is not None
            else "no position yet"
        )
        print(
            f"  vehicle {i}  port {port}  sysid {info['sysid']}  "
            f"{info['mode']:<10} armed={info['armed']}  {pos}"
        )

    print()
    if len(results) != count:
        print(f"RESULT: FAIL - {len(results)}/{count} vehicles responded")
        return 1

    print("Checking each endpoint has exactly one vehicle on it...")
    stacked = False
    for i, port in enumerate(ports):
        seen = sysids_on(port)
        if len(seen) > 1:
            print(f"  vehicle {i}  port {port}  FAIL - hears {len(seen)} "
                  f"system IDs {sorted(seen)}")
            stacked = True
        else:
            print(f"  vehicle {i}  port {port}  ok - one vehicle {sorted(seen)}")
    print()
    if stacked:
        print("RESULT: FAIL - duplicate SITL instances are sharing an endpoint.")
        print("        Run ./scripts/stop_swarm.sh, confirm it reports clean,")
        print("        then launch again. Test results are meaningless until then.")
        return 1

    sysids = [r["sysid"] for r in results.values()]
    if len(set(sysids)) != len(sysids):
        print(f"RESULT: FAIL - system IDs are not distinct: {sysids}")
        print("        FOLLOW cannot tell the vehicles apart. Check --auto-sysid.")
        return 1

    print(f"RESULT: PASS - {count} vehicles up with distinct system IDs {sorted(sysids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
