#!/usr/bin/env python3
"""Find the staged highway accident in a camera frame, and say where it is.

Two separable jobs:

  1. Is there anything red in this frame? The accident is the only saturated
     red in the world - asphalt is grey, markings white, grass green, sky
     pale - so a hue threshold is a legitimate detector here rather than a
     toy. It is emphatically not a general-purpose vehicle detector, and the
     moment this points at real video it has to be replaced.

  2. Where on the ground is it? This is the part worth doing carefully. The
     camera looks forward and down, so a detection near the top of the frame
     is near the horizon, where a small angular error becomes a huge ground
     error. The projection below therefore reports its own reliability
     instead of always answering: a ray whose downward component is too
     shallow gets a bearing and no range.
"""
import math

import cv2
import numpy as np

# Saturated red, both sides of the hue wrap. OpenCV's H is 0-180.
RED_LO_1, RED_HI_1 = (0, 120, 60), (8, 255, 255)
RED_LO_2, RED_HI_2 = (172, 120, 60), (180, 255, 255)

MIN_AREA_PX = 120          # smaller than this is noise or a distant speck
CAMERA_PITCH_RAD = 0.35    # must match the sensor pose in make_swarm_world.py
HORIZONTAL_FOV_RAD = 1.204

# A ray flatter than this is looking too near the horizon for its range to
# mean anything; and never project further than this many altitudes out.
MIN_DEPRESSION_SIN = math.sin(math.radians(12.0))
MAX_RANGE_ALTITUDES = 25.0


def find_red(rgb):
    """Return (area_px, cx, cy, boxes) for the largest red blob, or None."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, np.array(RED_LO_1, np.uint8),
                       np.array(RED_HI_1, np.uint8))
    mask |= cv2.inRange(hsv, np.array(RED_LO_2, np.uint8),
                        np.array(RED_HI_2, np.uint8))
    # Close small gaps so one car split by a highlight stays one blob.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    blobs = [(cv2.contourArea(c), c) for c in contours]
    blobs = [(a, c) for a, c in blobs if a >= MIN_AREA_PX]
    if not blobs:
        return None
    total = sum(a for a, _ in blobs)
    big = max(blobs, key=lambda t: t[0])
    # Centroid over every accepted blob: two cars and debris should localise
    # to the middle of the scene, not to whichever fragment is largest.
    m = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(m, [c for _, c in blobs], -1, 255, -1)
    ys, xs = np.nonzero(m)
    cx, cy = float(xs.mean()), float(ys.mean())
    boxes = [cv2.boundingRect(c) for _, c in blobs]
    return {"area_px": float(total), "largest_px": float(big[0]),
            "cx": cx, "cy": cy, "blobs": len(blobs), "boxes": boxes}


def _rot_ned_from_body(yaw, pitch, roll):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp,     cp * sr,                cp * cr),
    )


def project_to_ground(cx, cy, width, height, alt, yaw_deg,
                      pitch_deg=0.0, roll_deg=0.0):
    """Where does the ray through pixel (cx, cy) meet the ground?

    Returns a dict with the north/east offset from the vehicle, the slant
    range and a `reliable` flag. Angles in degrees; alt is height above the
    ground in metres.
    """
    if not alt or alt <= 0.5:
        return {"reliable": False, "why": "too low to project"}

    fx = (width / 2.0) / math.tan(HORIZONTAL_FOV_RAD / 2.0)
    fy = fx                                   # square pixels
    # Gazebo's camera looks along +X with +Y left and +Z up, so a pixel to the
    # right of centre is -Y and a pixel below centre is -Z. Expressed in the
    # body convention (x forward, y right, z down) both signs flip back.
    dx, dy, dz = 1.0, (cx - width / 2.0) / fx, (cy - height / 2.0) / fy

    # Mount pitch: rotate the camera axis down about the body y axis.
    t = CAMERA_PITCH_RAD
    bx = dx * math.cos(t) - dz * math.sin(t)
    by = dy
    bz = dx * math.sin(t) + dz * math.cos(t)

    R = _rot_ned_from_body(math.radians(yaw_deg), math.radians(pitch_deg),
                           math.radians(roll_deg))
    n = R[0][0] * bx + R[0][1] * by + R[0][2] * bz
    e = R[1][0] * bx + R[1][1] * by + R[1][2] * bz
    d = R[2][0] * bx + R[2][1] * by + R[2][2] * bz

    norm = math.sqrt(n * n + e * e + d * d)
    n, e, d = n / norm, e / norm, d / norm
    bearing = (math.degrees(math.atan2(e, n))) % 360.0

    if d < MIN_DEPRESSION_SIN:
        return {"reliable": False, "why": "too near the horizon to range",
                "bearing_deg": round(bearing, 1)}
    rng = alt / d
    if rng > MAX_RANGE_ALTITUDES * alt:
        return {"reliable": False, "why": "beyond usable range",
                "bearing_deg": round(bearing, 1)}
    return {
        "reliable": True,
        "north": round(n * rng, 1), "east": round(e * rng, 1),
        "range_m": round(rng, 1), "bearing_deg": round(bearing, 1),
        "depression_deg": round(math.degrees(math.asin(d)), 1),
    }


def annotate(rgb, hit, label):
    """Draw the detection onto a copy of the frame, for the evidence file."""
    img = np.ascontiguousarray(rgb[:, :, ::-1])     # to BGR for cv2
    for (x, y, w, h) in hit["boxes"]:
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 255), 2)
    cv2.drawMarker(img, (int(hit["cx"]), int(hit["cy"])), (0, 255, 255),
                   cv2.MARKER_CROSS, 18, 2)
    cv2.putText(img, label, (6, img.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.4, (0, 255, 255), 1, cv2.LINE_AA)
    return img
