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

DEFAULT_FORWARD = "GLOBAL_POSITION_INT"


def split_endpoint(spec, default_host="127.0.0.1"):
    """Accept "14550" or "10.0.0.7:14550" or "127.0.0.2:14550"."""
    spec = str(spec)
    host, _, port = spec.rpartition(":")
    return (host or default_host), int(port)


def connect(spec, label):
    """Bind and listen. `spec` may name the address as well as the port.

    On the aircraft each Raspberry Pi has its own address on the shared Wi-Fi
    and they all use the same port; binding a specific address rather than
    every address is what makes that arrangement expressible here.
    """
    host, port = split_endpoint(spec)
    conn = mavutil.mavlink_connection(f"udpin:{host}:{port}")
    print(f"  {label:<10} udpin:{host}:{port}")
    return conn


def connect_push(endpoint, label):
    """Fire-and-forget to a fixed address, with no handshake.

    The default mode waits to hear from each follower before writing, which is
    right when the far end is a MAVProxy that announces itself. It is wrong
    when the far end is another companion agent: two agents would each sit
    waiting for the other. A real Pi pushing position to known peers over
    Wi-Fi does not handshake either - it just sends.
    """
    host, port = split_endpoint(endpoint)
    conn = mavutil.mavlink_connection(f"udpout:{host}:{port}")
    print(f"  {label:<10} udpout:{host}:{port}")
    return conn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leader", default="14550",
                    help="where to listen: PORT, or ADDRESS:PORT to bind one "
                         "address (each aircraft has its own on real Wi-Fi)")
    ap.add_argument("--followers", default="14560",
                    help="comma-separated endpoints, PORT or ADDRESS:PORT")
    ap.add_argument("--rate", type=float, default=10.0,
                    help="max forwarded position messages per second")
    ap.add_argument("--loss", type=float, default=0.0,
                    help="drop this fraction of packets (0.0-1.0) to model a bad link")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="add this many seconds of latency to each packet")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--push", action="store_true",
                    help="send to the follower endpoints without waiting to "
                         "hear from them first - use when the far end is "
                         "another agent rather than a MAVProxy")
    ap.add_argument("--forward", default=DEFAULT_FORWARD,
                    help="comma-separated message types to relay. Add "
                         "HEARTBEAT when pushing to another agent, so the "
                         "far end can identify the leader.")
    ap.add_argument("--label", default="relay",
                    help="name for this agent in its log lines")
    ap.add_argument("--pidfile", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".run", "relay.pid"),
        help="where to write this process's PID")
    args = ap.parse_args()

    follower_ports = [p.strip() for p in args.followers.split(",") if p.strip()]
    forward_types = {t.strip().upper() for t in args.forward.split(",") if t.strip()}

    # Publish our PID so tests can stop exactly this process. Killing by
    # pattern instead would also match any shell whose command line mentions
    # this file - including the one that launched us.
    if args.pidfile:
        os.makedirs(os.path.dirname(args.pidfile), exist_ok=True)
        with open(args.pidfile, "w") as fh:
            fh.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(args.pidfile) and os.remove(args.pidfile))

    print(f"Connecting ({args.label}):")
    leader = connect(args.leader, "in")
    if args.push:
        followers = [connect_push(p, "out") for p in follower_ports]
    else:
        followers = [connect(p, "follower") for p in follower_ports]

        # A udpin socket only knows where to write after it has heard from the
        # other side, so wait for each follower's MAVProxy to say something
        # first. Push mode skips this: there is nothing there to announce
        # itself, only another agent listening.
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
    print(f"\nRelaying {sorted(forward_types)} from sysid {leader_sysid} "
          f"to {len(followers)} follower(s) at up to {args.rate:g} Hz{impair}")
    print("Ctrl+C to stop.\n")

    min_interval = 1.0 / args.rate if args.rate > 0 else 0.0
    last_sent = 0.0
    forwarded = dropped = 0
    last_report = time.time()

    try:
        while True:
            msg = leader.recv_match(type=list(forward_types), blocking=True, timeout=5)
            if msg is None:
                print("  (no position from leader in 5s - is it still running?)")
                continue
            # Only relay the leader's own position, never a follower's echo.
            if msg.get_srcSystem() != leader_sysid:
                continue

            now = time.time()
            if msg.get_type() == "GLOBAL_POSITION_INT":
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
                # Only a position message carries a position; with HEARTBEAT
                # in the forward set this line would otherwise crash on the
                # first heartbeat it happened to report on.
                where = ""
                if msg.get_type() == "GLOBAL_POSITION_INT":
                    where = (f"  (leader at {msg.lat/1e7:.6f},{msg.lon/1e7:.6f} "
                             f"alt {msg.relative_alt/1000.0:.1f}m)")
                print(f"  [{args.label}] forwarded {forwarded}"
                      + (f", dropped {dropped}" if dropped else "") + where)
                last_report = now
    except KeyboardInterrupt:
        print(f"\nStopped. Forwarded {forwarded}, dropped {dropped}.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
