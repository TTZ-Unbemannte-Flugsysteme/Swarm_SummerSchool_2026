#!/usr/bin/env python3
"""Survey patterns for the leader, and the runner that flies them.

The leader flies a list of waypoints; the followers are already a function of
the leader, so a formation survey needs no per-follower planning at all - that
is the whole argument for FOLLOW mode, and this is where it pays off.

Waypoints are built in north/east metres relative to the leader's *home*
(its spawn point), then converted to lat/lon once. Working in metres is what
lets worlds/scene.json - which is in world coordinates - be the single source
of truth for where the highway and the accident are.
"""
import json
import math
import os
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENE_PATH = os.path.join(REPO, "worlds", "scene.json")

ACCEPT_RADIUS_M = 12.0     # "arrived" - loose, because the leader tows a formation
LEG_TIMEOUT_S = 120.0      # a leg that takes longer than this has gone wrong


def load_scene():
    try:
        with open(SCENE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def meters_per_degree(lat):
    return 111320.0, 111320.0 * math.cos(math.radians(lat))


def offset_to_latlon(lat, lon, north, east):
    mn, me = meters_per_degree(lat)
    return lat + north / mn, lon + east / me


def latlon_to_offset(ref_lat, ref_lon, lat, lon):
    mn, me = meters_per_degree(ref_lat)
    return (lat - ref_lat) * mn, (lon - ref_lon) * me


def highway_legs(scene, leader_east, alt=30.0, start=40.0, end=340.0,
                 lanes=(0.0, 24.0)):
    """Out and back along the runway centreline - the 'highway'.

    `lanes` are east offsets from the centreline: the camera looks forward and
    down along the track, so one pass down the middle plus one offset pass
    covers the strip without a full area survey.
    """
    runway = (scene or {}).get("runway") or {"center_east": -29.0}
    centre_e = runway["center_east"] - leader_east   # world east -> home-relative
    pts = []
    for i, lane in enumerate(lanes):
        e = centre_e + lane
        legs = [(start, e), (end, e)] if i % 2 == 0 else [(end, e), (start, e)]
        pts.extend(legs)
    return [{"north": n, "east": e, "alt": alt} for n, e in pts]


def area_lawnmower(alt=35.0, north_from=40.0, north_to=280.0,
                   east_half=80.0, spacing=80.0, leader_east=0.0,
                   scene=None):
    """Boustrophedon over a rectangle - the classic area-coverage pattern.

    Lanes run north-south because that is the long axis here, which keeps the
    number of turns down; every turn is where a formation is most likely to
    lose station.
    """
    runway = (scene or {}).get("runway") or {"center_east": -29.0}
    centre_e = runway["center_east"] - leader_east
    lanes, e = [], -east_half
    while e <= east_half + 1e-6:
        lanes.append(centre_e + e)
        e += spacing
    pts = []
    for i, lane in enumerate(lanes):
        legs = [(north_from, lane), (north_to, lane)] if i % 2 == 0 \
            else [(north_to, lane), (north_from, lane)]
        pts.extend(legs)
    return [{"north": n, "east": ee, "alt": alt} for n, ee in pts]


class Mission:
    """One survey in progress, flown waypoint by waypoint."""

    def __init__(self, name, waypoints, home_lat, home_lon, speed=8.0):
        self.name = name
        self.waypoints = waypoints
        self.home = (home_lat, home_lon)
        self.speed = speed
        self.index = 0
        self.started = time.time()
        self.finished = None
        self.aborted = False
        self.note = None
        self.leg_started = time.time()

    def target_latlon(self):
        if self.index >= len(self.waypoints):
            return None
        wp = self.waypoints[self.index]
        lat, lon = offset_to_latlon(self.home[0], self.home[1],
                                    wp["north"], wp["east"])
        return lat, lon, wp["alt"]

    def state(self):
        wp = self.waypoints[self.index] if self.index < len(self.waypoints) else None
        return {
            "name": self.name,
            "leg": self.index + 1 if wp else len(self.waypoints),
            "legs": len(self.waypoints),
            "waypoints": self.waypoints,
            "target": wp,
            "running": self.finished is None and not self.aborted,
            "aborted": self.aborted,
            "elapsed": round(time.time() - self.started, 1),
            "note": self.note,
        }
