#!/usr/bin/env python3
"""Forward the leader's position to every follower.

This is the one piece of the simulation that is also destined for the real
Raspberry Pi. In simulation it bridges UDP endpoints on one host; on the
aircraft the same loop reads the leader's position off the Wi-Fi network and
writes it into the local flight controller's UART. The logic is identical -
only the endpoint strings change.

Raw message bytes are forwarded untouched, which preserves the leader's
system ID. That matters: a follower's FOLL_SYSID only matches if the packet
still claims to come from the leader.

    python3 relay.py --leader 14550 --followers 14560,14570
    python3 relay.py --leader 14550 --followers 14560 --rate 5 --loss 0.3
"""
import argparse
import atexit
import os
import random
import sys
import time

from pymavlink import mavutil

FORWARD_TYPES = {"GLOBAL_POSITION_INT"}


def connect(port, label):
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{port}")
    print(f"  {label:<10} udpin:127.0.0.1:{port}")
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader", type=int, default=14550, help="leader's UDP port")
    ap.add_argument("--followers", default="14560", help="comma-separated follower ports")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="max forwarded position messages per second")
    ap.add_argument("--loss", type=float, default=0.0,
                    help="drop this fraction of packets (0.0-1.0) to model a bad link")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="add this many seconds of latency to each packet")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--pidfile", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".run", "relay.pid"),
        help="where to write this process's PID")
    args = ap.parse_args()

    follower_ports = [int(p) for p in args.followers.split(",") if p.strip()]

    # Publish our PID so tests can stop exactly this process. Killing by
    # pattern instead would also match any shell whose command line mentions
    # this file - including the one that launched us.
    if args.pidfile:
        os.makedirs(os.path.dirname(args.pidfile), exist_ok=True)
        with open(args.pidfile, "w") as fh:
            fh.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(args.pidfile) and os.remove(args.pidfile))

    print("Connecting:")
    leader = connect(args.leader, "leader")
    followers = [connect(p, f"follower") for p in follower_ports]

    # A udpin socket only knows where to write after it has heard from the
    # other side, so wait for each follower's MAVProxy to say something first.
    print("\nWaiting for each endpoint to announce itself...")
    for port, conn in zip(follower_ports, followers):
        hb = conn.wait_heartbeat(timeout=15)
        if hb is None:
            print(f"  FAIL: no heartbeat from follower on {port}")
            return 1
        print(f"  follower on {port} is sysid {conn.target_system}")

    hb = leader.wait_heartbeat(timeout=15)
    if hb is None:
        print(f"  FAIL: no heartbeat from leader on {args.leader}")
        return 1
    leader_sysid = leader.target_system
    print(f"  leader on {args.leader} is sysid {leader_sysid}")

    impair = ""
    if args.loss or args.delay:
        impair = f"  [link impairment: {args.loss:.0%} loss, {args.delay*1000:.0f}ms delay]"
    print(f"\nRelaying {sorted(FORWARD_TYPES)} from sysid {leader_sysid} "
          f"to {len(followers)} follower(s) at up to {args.rate:g} Hz{impair}")
    print("Ctrl+C to stop.\n")

    min_interval = 1.0 / args.rate if args.rate > 0 else 0.0
    last_sent = 0.0
    forwarded = dropped = 0
    last_report = time.time()

    try:
        while True:
            msg = leader.recv_match(type=list(FORWARD_TYPES), blocking=True, timeout=5)
            if msg is None:
                print("  (no position from leader in 5s - is it still running?)")
                continue
            # Only relay the leader's own position, never a follower's echo.
            if msg.get_srcSystem() != leader_sysid:
                continue

            now = time.time()
            if now - last_sent < min_interval:
                continue
            last_sent = now

            if args.loss and random.random() < args.loss:
                dropped += 1
                continue
            if args.delay:
                time.sleep(args.delay)

            raw = msg.get_msgbuf()
            for conn in followers:
                conn.write(raw)
            forwarded += 1

            if not args.quiet and now - last_report >= 2.0:
                print(f"  forwarded {forwarded} positions"
                      + (f", dropped {dropped}" if dropped else "")
                      + f"  (leader at {msg.lat/1e7:.6f},{msg.lon/1e7:.6f} "
                        f"alt {msg.relative_alt/1000.0:.1f}m)")
                last_report = now
    except KeyboardInterrupt:
        print(f"\nStopped. Forwarded {forwarded}, dropped {dropped}.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
