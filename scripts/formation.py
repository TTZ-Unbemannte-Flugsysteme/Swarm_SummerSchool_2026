#!/usr/bin/env python3
"""Where each follower sits, for any number of drones.

This used to be a list of four offsets indexed modulo its own length, which
silently gave the fifth and sixth drones the same stations as the first and
second. ArduPilot's FOLLOW mode has no idea other followers exist - each one
steers to its own offset from the leader and nothing else - so two drones
handed the same offset are two drones commanded to the same point in the air.

The shape is a V: followers alternate left and right, each pair one rank
further back. Rank r sits RANK_BACK*r behind the leader and RANK_SIDE*r to
one side, which keeps every drone clear of every other by construction and
widens the formation as it grows, rather than packing more aircraft into the
same volume.

    python3 scripts/formation.py 9      # print the stations for 9 drones
"""
import math
import sys

# Deliberately generous. The binding constraint is not the airframe, it is the
# *relative* error between two non-RTK GNSS receivers, which is metres - see
# the architecture document. Tighten from measurement, never from optimism.
RANK_BACK = 25.0     # metres further back per rank
RANK_SIDE = 20.0     # metres further out per rank


def station(k, rank_back=RANK_BACK, rank_side=RANK_SIDE):
    """Station for the k-th follower, k counting from 0.

    Returns (north, east) in metres relative to the leader. North is negative
    because followers trail. Alternates right, left, right, left...
    """
    if k < 0:
        raise ValueError("follower index must be >= 0")
    rank = k // 2 + 1
    # Left first, so drone 2 keeps the -25N -20E station quoted in the
    # README and already measured; flipping it would invalidate those numbers
    # for no benefit.
    side = -1 if k % 2 == 0 else 1
    return (-rank_back * rank, side * rank_side * rank)


def stations(n_followers, **kw):
    return [station(k, **kw) for k in range(n_followers)]


def closest_pair(n_followers, **kw):
    """Smallest distance between any two stations, the leader included.

    The number worth checking before flying a bigger swarm: it has to stay
    comfortably above the relative GNSS error, or the formation is inside its
    own position uncertainty and aircraft will drift through each other's
    stations.
    """
    pts = [(0.0, 0.0)] + stations(n_followers, **kw)
    best = float("inf")
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.dist(pts[i], pts[j])
            best = min(best, d)
    return best


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"{n} drones: 1 leader + {n - 1} followers\n")
    print(f"  {'drone':<7}{'north':>9}{'east':>9}   station")
    print(f"  {'1':<7}{0.0:9.1f}{0.0:9.1f}   leader")
    for k, (north, east) in enumerate(stations(n - 1)):
        print(f"  {k + 2:<7}{north:9.1f}{east:9.1f}   rank {k // 2 + 1}"
              f" {'left' if k % 2 == 0 else 'right'}")
    sep = closest_pair(n - 1)
    print(f"\n  closest two stations: {sep:.1f} m")
    print("  " + ("comfortable against metre-class relative GNSS error"
                  if sep >= 15 else "TOO TIGHT for non-RTK receivers"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
