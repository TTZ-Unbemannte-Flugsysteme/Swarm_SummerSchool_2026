#!/usr/bin/env python3
"""Per-drone camera streams, from Gazebo to the browser.

Each vehicle model carries a camera sensor publishing on its own gz-transport
topic (see scripts/make_swarm_world.py). This subscribes with the gz-transport
Python bindings, JPEG-encodes frames with OpenCV, and hands them to the HTTP
server as multipart/x-mixed-replace - the MJPEG trick, which for three small
feeds over loopback needs no signalling, no negotiation and nothing extra in
the page. WebRTC is the right answer for video off a real aircraft; it is the
wrong answer for this.

Frames are dropped, never queued. A viewer that stalls must not be able to
make this process accumulate memory or serve video that is seconds stale.

Everything here is optional: if the bindings are missing or no camera is
publishing, video_available() is False and the dashboard runs exactly as it
did before.
"""
import os
import threading
import time

# gz's generated protobuf modules predate the installed protobuf runtime, which
# refuses to build descriptors from them. The pure-Python implementation reads
# them fine, and it has to be selected before the modules are imported. Only
# the image messages go through protobuf here, so the slower path costs little.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# Same reason as scripts/env.sh: gz-transport's multicast discovery can pick a
# down interface and then never deliver a frame, while still reporting the
# topic as advertised. Set here as well so the dashboard works when started by
# hand, outside flyit.
os.environ.setdefault("GZ_IP", "127.0.0.1")

IMPORT_ERROR = None
try:
    import cv2
    import numpy as np
    from gz.msgs10.image_pb2 import Image
    from gz.transport13 import Node
except Exception as exc:                                  # pragma: no cover
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

JPEG_QUALITY = 70
STALE_AFTER = 3.0


def video_available():
    return IMPORT_ERROR is None


class Camera:
    """Newest JPEG frame from one drone's camera, and nothing else."""

    def __init__(self, index, topic):
        self.index = index
        self.topic = topic
        self.lock = threading.Lock()
        self.jpeg = None
        self.seq = 0            # lets a streaming client wait for a new frame
        self.frames = 0
        self.last_frame = 0.0
        self.rate_mark = (time.time(), 0)   # (when, frames) for the fps estimate
        self.fps = None
        self.width = self.height = None
        self.error = None
        self.detect_every = 0        # 0 = no detection on this camera
        self.on_detect = None
        self.new_frame = threading.Condition(self.lock)

    def on_image(self, msg):
        try:
            # PixelFormatId 3 is RGB_INT8, which is what the SDF asks for.
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            expected = msg.height * msg.width * 3
            if arr.size < expected:
                self.error = f"short frame: {arr.size} < {expected}"
                return
            rgb = arr[:expected].reshape((msg.height, msg.width, 3))
            if self.on_detect and self.detect_every and \
                    self.frames % self.detect_every == 0:
                try:
                    self.on_detect(self.index, rgb)
                except Exception as exc:
                    self.error = f"detector: {type(exc).__name__}: {exc}"
            ok, buf = cv2.imencode(".jpg", rgb[:, :, ::-1],
                                   [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            if not ok:
                self.error = "cv2.imencode failed"
                return
            with self.lock:
                self.jpeg = buf.tobytes()
                self.seq += 1
                self.frames += 1
                self.last_frame = time.time()
                # Measured, not assumed: the sensor's update_rate is in *sim*
                # time, so a real-time factor below 1.0 shows up right here.
                since, at = self.rate_mark
                if self.last_frame - since >= 2.0:
                    self.fps = round((self.frames - at) / (self.last_frame - since), 1)
                    self.rate_mark = (self.last_frame, self.frames)
                self.width, self.height = msg.width, msg.height
                self.error = None
                self.new_frame.notify_all()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def wait_for_frame(self, after_seq, timeout=5.0):
        """Block until a frame newer than after_seq exists. Returns (jpeg, seq)."""
        with self.lock:
            if self.seq <= after_seq:
                self.new_frame.wait(timeout)
            return self.jpeg, self.seq

    def snapshot(self):
        with self.lock:
            age = time.time() - self.last_frame if self.last_frame else None
            return {
                "index": self.index,
                "topic": self.topic,
                "live": age is not None and age < STALE_AFTER,
                "frames": self.frames,
                "fps": self.fps,
                "age": round(age, 2) if age is not None else None,
                "width": self.width,
                "height": self.height,
                "error": self.error,
            }


class VideoHub:
    """One gz-transport node, one Camera per drone.

    The node must outlive the subscriptions - a garbage-collected Node silently
    stops delivering callbacks - so it is held here for the process lifetime.
    """

    def __init__(self, count, topic_fmt="/drone{i}/camera"):
        self.cameras = {}
        self.error = IMPORT_ERROR
        self.node = None
        if IMPORT_ERROR is not None:
            return
        self.node = Node()
        for i in range(count):
            topic = topic_fmt.format(i=i)
            cam = Camera(i, topic)
            if self.node.subscribe(Image, topic, cam.on_image):
                self.cameras[i] = cam
            else:
                cam.error = "subscribe failed"
                self.cameras[i] = cam

    def get(self, index):
        return self.cameras.get(index)

    def status(self):
        return {
            "available": IMPORT_ERROR is None,
            "error": self.error,
            "cameras": [c.snapshot() for c in
                        sorted(self.cameras.values(), key=lambda c: c.index)],
        }


BOUNDARY = "swarmframe"


def stream_mjpeg(camera, wfile, should_stop=None):
    """Write an endless multipart JPEG stream. Returns when the client goes."""
    seq = -1
    while True:
        if should_stop is not None and should_stop():
            return
        jpeg, seq = camera.wait_for_frame(seq)
        if jpeg is None:
            continue
        wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
        wfile.write(jpeg)
        wfile.write(b"\r\n")
        wfile.flush()


# --------------------------------------------------------------------------
# Detection hook
#
# Detection runs inside the frame callback, so it must stay cheap: a 320x240
# hue threshold is well under a millisecond, but it is still gated to every
# Nth frame. If this ever becomes a real detector, it moves to its own thread
# with a single-slot queue - never a growing one.
DETECT_EVERY = 3


def attach_detector(hub, callback, every=DETECT_EVERY):
    """Call `callback(index, rgb)` on every `every`-th frame of each camera."""
    for cam in hub.cameras.values():
        cam.detect_every = every
        cam.on_detect = callback
