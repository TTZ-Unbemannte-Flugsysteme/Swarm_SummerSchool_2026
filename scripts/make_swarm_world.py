#!/usr/bin/env python3
"""Generate a Gazebo world holding N ArduPilot vehicles.

The stock ardupilot_gazebo model hardcodes one FDM port (9002), so including it
twice gives two vehicles fighting over the same socket - the second never
connects. This clones the model once per vehicle with its own port, matching
what SITL expects for instance i:

    SITL -I i   sends FDM to   9002 + 10*i     <- the plugin's fdm_port_in

    python3 scripts/make_swarm_world.py 3

Writes models/iris_v0..N-1/ and worlds/swarm_runway.sdf, both regenerated from
scratch each run. Spacing lives in the world here, not in SITL's
--custom-location: with Gazebo driving the physics, the model pose IS the
vehicle's position, and a custom home on top of it would double-count.
"""
import argparse
import json
import math
import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SSS_ROOT = os.environ.get("SSS_ROOT", "/home/ttz/workspaces/SSS_2026")
PLUGIN_DIR = os.environ.get("ARDUPILOT_GAZEBO",
                            os.path.join(SSS_ROOT, "ardupilot_gazebo"))
BASE_MODEL = "iris_with_ardupilot"
BASE_WORLD = "iris_runway.sdf"

# The stock world's runway: a 1500 x 100 m strip whose long axis runs
# north-south, centred 545 m north of the world origin. The vehicles spawn on
# it, so for the inspection missions this strip *is* the highway. Read off
# models/runway/model.sdf and the include pose in the stock world - if either
# changes upstream, these numbers have to change with them.
RUNWAY = {
    "center_east": -29.0, "center_north": 545.0,
    "length": 1500.0, "width": 100.0, "heading_deg": 3.0,
}

# A staged accident to find: two cars and some debris, in saturated red and
# yellow. Nothing else in this world is either colour - the asphalt is grey,
# the markings white, the grass green, the sky pale - which is what makes a
# plain hue threshold a workable detector rather than a toy.
ACCIDENT = """
    <model name="highway_accident">
      <static>true</static>
      <pose degrees="true">{east} {north} 0 0 0 {heading}</pose>
      <link name="link">
        <visual name="car_a">
          <pose>0 0 0.7 0 0 0</pose>
          <geometry><box><size>4.2 1.8 1.4</size></box></geometry>
          <material>
            <ambient>0.55 0.02 0.02 1</ambient>
            <diffuse>0.85 0.03 0.03 1</diffuse>
            <specular>0.2 0.1 0.1 1</specular>
          </material>
        </visual>
        <visual name="car_b">
          <pose degrees="true">3.0 4.2 0.7 0 0 52</pose>
          <geometry><box><size>4.2 1.8 1.4</size></box></geometry>
          <material>
            <ambient>0.55 0.02 0.02 1</ambient>
            <diffuse>0.85 0.03 0.03 1</diffuse>
            <specular>0.2 0.1 0.1 1</specular>
          </material>
        </visual>
        <visual name="debris_a">
          <pose degrees="true">1.4 2.1 0.15 0 0 18</pose>
          <geometry><box><size>1.6 1.2 0.3</size></box></geometry>
          <material>
            <ambient>0.6 0.5 0.02 1</ambient>
            <diffuse>0.95 0.8 0.03 1</diffuse>
          </material>
        </visual>
        <visual name="debris_b">
          <pose degrees="true">-2.6 1.2 0.12 0 0 -35</pose>
          <geometry><box><size>1.1 0.9 0.24</size></box></geometry>
          <material>
            <ambient>0.6 0.5 0.02 1</ambient>
            <diffuse>0.95 0.8 0.03 1</diffuse>
          </material>
        </visual>
        <collision name="collision">
          <pose>0 0 0.7 0 0 0</pose>
          <geometry><box><size>4.2 1.8 1.4</size></box></geometry>
        </collision>
      </link>
    </model>
"""

# A forward-down camera per vehicle, on its own topic so the dashboard can tell
# the drones apart. Deliberately small and slow: camera sensors are rendered,
# and the ardupilot_gazebo plugin is lock-stepped with SITL, so a renderer that
# cannot keep up slows the physics clock and corrupts the flight measurements
# this repo exists to make. 320x240 at 10 Hz is what three of these cost least
# at; check the real-time factor before raising either number.
#
# The link carries a token 10 g so Gazebo does not warn about zero mass. It is
# fixed to the airframe, so the solver folds it into the parent body; against a
# ~1.5 kg iris it is not a dynamically meaningful change.
CAMERA_LINK = """
    <link name="camera_link">
      <pose>0.12 0 0.0 0 0.35 0</pose>
      <inertial>
        <mass>0.01</mass>
        <inertia>
          <ixx>1e-6</ixx><iyy>1e-6</iyy><izz>1e-6</izz>
          <ixy>0</ixy><ixz>0</ixz><iyz>0</iyz>
        </inertia>
      </inertial>
      <visual name="camera_visual">
        <geometry><box><size>0.03 0.03 0.03</size></box></geometry>
        <material>
          <ambient>0.1 0.1 0.1 1</ambient>
          <diffuse>0.15 0.15 0.15 1</diffuse>
        </material>
      </visual>
      <sensor name="camera" type="camera">
        <topic>{topic}</topic>
        <update_rate>{rate}</update_rate>
        <always_on>1</always_on>
        <visualize>0</visualize>
        <camera>
          <horizontal_fov>1.204</horizontal_fov>
          <image>
            <width>{width}</width>
            <height>{height}</height>
            <format>R8G8B8</format>
          </image>
          <clip><near>0.1</near><far>500</far></clip>
        </camera>
      </sensor>
    </link>
    <joint name="camera_joint" type="fixed">
      <parent>iris_with_standoffs::base_link</parent>
      <child>camera_link</child>
    </joint>
"""

MODEL_CONFIG = """<?xml version="1.0"?>
<model>
  <name>iris_v{i}</name>
  <version>1.0</version>
  <sdf version="1.9">model.sdf</sdf>
  <description>
    Generated by scripts/make_swarm_world.py - iris on FDM port {port}
    (ArduPilot SITL instance {i}). Do not edit; regenerate instead.
  </description>
</model>
"""


def camera_topic(index):
    return f"drone{index}/camera"


def clone_model(index, port, out_root, camera=True, rate=10, size=(320, 240)):
    src = os.path.join(PLUGIN_DIR, "models", BASE_MODEL, "model.sdf")
    with open(src) as fh:
        sdf = fh.read()

    name = f"iris_v{index}"
    sdf, n_name = re.subn(r'<model name="[^"]+"', f'<model name="{name}"', sdf, count=1)
    sdf, n_port = re.subn(r"<fdm_port_in>\s*\d+\s*</fdm_port_in>",
                          f"<fdm_port_in>{port}</fdm_port_in>", sdf, count=1)
    if n_name != 1 or n_port != 1:
        raise SystemExit(
            f"ERROR: could not patch {src}\n"
            f"  model name replaced: {n_name}, fdm_port_in replaced: {n_port}\n"
            f"  The upstream model changed shape; update this generator.")

    if camera:
        block = CAMERA_LINK.format(topic=camera_topic(index), rate=rate,
                                   width=size[0], height=size[1])
        sdf, n_cam = re.subn(r"(\s*)</model>", block + r"\1</model>", sdf, count=1)
        if n_cam != 1:
            raise SystemExit(f"ERROR: no closing </model> to extend in {src}")

    dest = os.path.join(out_root, name)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "model.sdf"), "w") as fh:
        fh.write(sdf)
    with open(os.path.join(dest, "model.config"), "w") as fh:
        fh.write(MODEL_CONFIG.format(i=index, port=port))
    return name


def build_world(count, spacing, out_path, accident=None):
    src = os.path.join(PLUGIN_DIR, "worlds", BASE_WORLD)
    with open(src) as fh:
        world = fh.read()

    # Drop whatever single vehicle the stock world includes.
    world, dropped = re.subn(
        r"\n\s*<include>\s*<uri>model://iris[^<]*</uri>.*?</include>\n",
        "\n", world, flags=re.S)
    if dropped != 1:
        raise SystemExit(f"ERROR: expected one iris include in {src}, found {dropped}")

    world = re.sub(r'<world name="[^"]+"', '<world name="swarm_runway"', world, count=1)

    # Lay the vehicles out east-west, centred on the world origin.
    first = -spacing * (count - 1) / 2.0
    blocks = []
    for i in range(count):
        east = first + i * spacing
        blocks.append(
            f"    <!-- vehicle {i}: FDM port {9002 + 10 * i} -->\n"
            f"    <include>\n"
            f"      <uri>model://iris_v{i}</uri>\n"
            f"      <pose degrees=\"true\">{east:g} 0 0.195 0 0 90</pose>\n"
            f"    </include>\n")
    if accident is not None:
        blocks.append(ACCIDENT.format(east=accident["east"],
                                      north=accident["north"],
                                      heading=accident["heading_deg"]))
    world = world.replace("  </world>", "\n".join(blocks) + "\n  </world>", 1)

    with open(out_path, "w") as fh:
        fh.write(world)


def write_scene(path, count, spacing, accident):
    """Record where everything is, in world coordinates.

    The mission planner needs to know where the highway runs and the dashboard
    needs to draw it; the accident's position is also the answer key for
    checking whether the detector actually found the right thing. Writing it
    once here keeps those three from carrying separate copies of the same
    numbers and drifting apart.
    """
    first = -spacing * (count - 1) / 2.0
    scene = {
        "generated_by": "scripts/make_swarm_world.py",
        "spacing_m": spacing,
        "vehicles": [{"index": i, "east": first + i * spacing, "north": 0.0}
                     for i in range(count)],
        "runway": dict(RUNWAY),
        "accident": dict(accident) if accident else None,
    }
    with open(path, "w") as fh:
        json.dump(scene, fh, indent=2)
    return scene


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("count", nargs="?", type=int, default=3)
    ap.add_argument("--spacing", type=float, default=15.0,
                    help="metres between vehicles on the ground")
    ap.add_argument("--no-cameras", action="store_true",
                    help="omit camera sensors (measure the real-time factor "
                         "with and without to see what they cost)")
    ap.add_argument("--camera-rate", type=float, default=10.0)
    ap.add_argument("--camera-size", default="320x240")
    ap.add_argument("--no-accident", action="store_true",
                    help="leave the highway clear (the negative control for "
                         "the detector: it must then report nothing)")
    ap.add_argument("--accident-north", type=float, default=250.0,
                    help="metres north of the spawn line to stage it")
    args = ap.parse_args()
    cw, _, ch = args.camera_size.partition("x")
    cam_size = (int(cw), int(ch))

    if not os.path.isdir(PLUGIN_DIR):
        raise SystemExit(f"ERROR: ardupilot_gazebo not found at {PLUGIN_DIR}")

    models_root = os.path.join(REPO, "models")
    for stale in os.listdir(models_root) if os.path.isdir(models_root) else []:
        if re.fullmatch(r"iris_v\d+", stale):
            shutil.rmtree(os.path.join(models_root, stale))
    os.makedirs(models_root, exist_ok=True)
    os.makedirs(os.path.join(REPO, "worlds"), exist_ok=True)

    print(f"Generating {args.count} vehicle(s) from {BASE_MODEL}:")
    for i in range(args.count):
        port = 9002 + 10 * i
        name = clone_model(i, port, models_root, camera=not args.no_cameras,
                           rate=args.camera_rate, size=cam_size)
        cam = "no camera" if args.no_cameras else \
            f"camera {cam_size[0]}x{cam_size[1]}@{args.camera_rate:g}Hz " \
            f"-> {camera_topic(i)}"
        print(f"  models/{name}/  fdm_port_in {port}  (SITL -I {i})  {cam}")

    accident = None
    if not args.no_accident:
        accident = {"east": RUNWAY["center_east"],
                    "north": args.accident_north,
                    "heading_deg": RUNWAY["heading_deg"] + 8.0}

    world_path = os.path.join(REPO, "worlds", "swarm_runway.sdf")
    build_world(args.count, args.spacing, world_path, accident)
    print(f"  worlds/swarm_runway.sdf  ({args.count} vehicles, "
          f"{args.spacing:g}m apart)")

    scene_path = os.path.join(REPO, "worlds", "scene.json")
    write_scene(scene_path, args.count, args.spacing, accident)
    if accident:
        print(f"  worlds/scene.json        accident staged at "
              f"{accident['north']:g}m north, {accident['east']:g}m east "
              f"(on the runway centreline)")
    else:
        print(f"  worlds/scene.json        no accident (negative control)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
