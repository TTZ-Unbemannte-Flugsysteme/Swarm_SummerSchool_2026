#!/usr/bin/env python3
"""Live status server for the swarm simulation.

One reader thread per vehicle keeps the latest MAVLink state; an HTTP server
hands that out as JSON and serves the dashboard page.

    python3 dashboard/server.py --vehicles 2 --http-port 8760

Endpoints:
    /             the dashboard page
    /api/status   current state of every vehicle plus formation error
"""
import argparse
import json
import math
import os
import sys
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pymavlink import mavutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import control as control_mod
import detect as detect_mod
import mission as mission_mod
import video as video_mod

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RELAY_PIDFILE = os.path.join(REPO, ".run", "relay.pid")

# How much error history the page can chart.
HISTORY_SECONDS = 180


def session_dir():
    """This run's session directory, so evidence lands beside the flight log."""
    link = os.path.join(REPO, ".run", "latest")
    try:
        return os.path.realpath(link) if os.path.exists(link) else None
    except OSError:
        return None

# pymavlink ships this already keyed by mode number -> name. Inverting it
# (the obvious-looking move) silently yields "mode 0" for everything.
def mode_name(custom_mode):
    return mavutil.mode_mapping_acm.get(custom_mode, f"mode {custom_mode}")


class VehicleState:
    """Latest known state of one vehicle, updated by its reader thread."""

    def __init__(self, index, port):
        self.index = index
        self.port = port
        self.lock = threading.Lock()
        # The reader thread owns this socket; manual control sends on the same
        # one, because a UDP port has exactly one owner. send_lock keeps the
        # two directions from interleaving inside pymavlink's mav object.
        self.conn = None
        self.send_lock = threading.Lock()
        self.sysid = None
        self.mode = None
        self.armed = False
        self.lat = None
        self.lon = None
        self.alt = None
        self.heading = None
        self.groundspeed = None
        self.gps_fix = None
        self.satellites = None
        self.battery_v = None
        self.battery_pct = None
        self.vn = self.ve = self.vd = None   # m/s, NED
        self.roll = None
        self.pitch = None
        self.yaw = None
        self.climb = None
        self.ekf_ok = None
        # Last COMMAND_ACK per command id. The reader thread owns this socket,
        # so it is the only place acks can be seen - without capturing them
        # here, a rejected arm or takeoff fails completely silently.
        self.acks = {}
        # Where this vehicle started. Captured while it is still disarmed on
        # the ground, which is the one moment its position is unambiguously
        # its spawn point - survey patterns are planned as offsets from here.
        self.home_lat = None
        self.home_lon = None
        # The autopilot's most recent complaint. "arm: failed" on its own
        # sends you to the logs; "arm: failed - PreArm: EKF3 not ready" does
        # not, so the refusal reason travels with the refusal.
        self.last_text = None
        self.last_text_at = 0.0
        self.last_seen = 0.0
        # FOLL_* parameters, fetched once the vehicle is talking.
        self.follow = {}
        self.params_fetched = False

    def snapshot(self):
        with self.lock:
            online = (time.time() - self.last_seen) < 4.0 if self.last_seen else False
            return {
                "index": self.index,
                "port": self.port,
                "sysid": self.sysid,
                "online": online,
                "mode": self.mode,
                "armed": self.armed,
                "lat": self.lat,
                "lon": self.lon,
                "alt": self.alt,
                "heading": self.heading,
                "groundspeed": self.groundspeed,
                "gps_fix": self.gps_fix,
                "satellites": self.satellites,
                "battery_v": self.battery_v,
                "battery_pct": self.battery_pct,
                "roll": self.roll,
                "pitch": self.pitch,
                "yaw": self.yaw,
                "climb": self.climb,
                "ekf_ok": self.ekf_ok,
                "home_lat": self.home_lat,
                "home_lon": self.home_lon,
                "last_text": self.last_text,
                "flyable": bool(self.ekf_ok) and (self.gps_fix or 0) >= 3,
                "age": round(time.time() - self.last_seen, 1) if self.last_seen else None,
                "follow": dict(self.follow),
            }


def reader(state):
    """Keep one vehicle's state current. Reconnects on its own if SITL restarts."""
    conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{state.port}")
    state.conn = conn
    wanted = ("FOLL_ENABLE", "FOLL_SYSID", "FOLL_OFS_X", "FOLL_OFS_Y",
              "FOLL_OFS_Z", "FOLL_OFS_TYPE")
    last_param_request = 0.0
    last_home_request = 0.0

    while True:
        msg = conn.recv_match(blocking=True, timeout=2)
        now = time.time()

        # Ask for the FOLLOW parameters once the vehicle is up, and keep asking
        # until they all arrive - a single request can be dropped.
        if state.sysid is not None and not state.params_fetched \
                and now - last_param_request > 4.0:
            for name in wanted:
                conn.mav.param_request_read_send(
                    conn.target_system, conn.target_component, name.encode(), -1)
            last_param_request = now

        # Ask the autopilot where home is, rather than only inferring it from
        # having watched the vehicle sit on the ground disarmed. Restarting
        # the dashboard mid-flight otherwise leaves home unknown for the rest
        # of the session, which silently breaks every survey.
        if state.home_lat is None and now - last_home_request > 5.0:
            conn.mav.command_long_send(
                conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_CMD_GET_HOME_POSITION, 0, 0, 0, 0, 0, 0, 0, 0)
            last_home_request = now

        if msg is None:
            continue
        mtype = msg.get_type()

        with state.lock:
            if mtype != "BAD_DATA":
                state.last_seen = now
                if state.sysid is None:
                    state.sysid = msg.get_srcSystem()

            if mtype == "HEARTBEAT":
                state.mode = mode_name(msg.custom_mode)
                state.armed = bool(msg.base_mode
                                   & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            elif mtype == "GLOBAL_POSITION_INT":
                state.lat = msg.lat / 1e7
                state.lon = msg.lon / 1e7
                state.alt = msg.relative_alt / 1000.0
                state.heading = msg.hdg / 100.0 if msg.hdg != 65535 else None
                state.vn, state.ve, state.vd = (msg.vx / 100.0, msg.vy / 100.0,
                                                msg.vz / 100.0)
                if state.home_lat is None and not state.armed and state.lat:
                    state.home_lat, state.home_lon = state.lat, state.lon
            elif mtype == "VFR_HUD":
                state.groundspeed = msg.groundspeed
                state.climb = msg.climb
            elif mtype == "ATTITUDE":
                state.roll = math.degrees(msg.roll)
                state.pitch = math.degrees(msg.pitch)
                # A yaw of -1e-14 rad becomes 359.99999... degrees, which
                # displays as "360". Round first, then wrap.
                state.yaw = round(math.degrees(msg.yaw) % 360.0, 1) % 360.0
            elif mtype == "EKF_STATUS_REPORT":
                # "Healthy enough to fly on" - position and velocity estimates
                # both good and no variance flagged.
                flags = msg.flags
                need = (mavutil.mavlink.EKF_ATTITUDE
                        | mavutil.mavlink.EKF_VELOCITY_HORIZ
                        | mavutil.mavlink.EKF_POS_HORIZ_REL
                        | mavutil.mavlink.EKF_PRED_POS_HORIZ_REL)
                state.ekf_ok = bool(flags & need == need)
            elif mtype == "GPS_RAW_INT":
                state.gps_fix = msg.fix_type
                state.satellites = msg.satellites_visible
            elif mtype == "SYS_STATUS":
                state.battery_v = msg.voltage_battery / 1000.0 \
                    if msg.voltage_battery != 65535 else None
                state.battery_pct = msg.battery_remaining \
                    if msg.battery_remaining != -1 else None
            elif mtype == "HOME_POSITION":
                state.home_lat = msg.latitude / 1e7
                state.home_lon = msg.longitude / 1e7
            elif mtype == "STATUSTEXT":
                if msg.severity <= 4:            # warning or worse
                    state.last_text = msg.text.strip()
                    state.last_text_at = now
            elif mtype == "COMMAND_ACK":
                state.acks[msg.command] = (msg.result, now)
            elif mtype == "PARAM_VALUE":
                name = msg.param_id.strip("\x00")
                if name in wanted:
                    state.follow[name] = msg.param_value
                    if all(k in state.follow for k in wanted):
                        state.params_fetched = True


class Sightings:
    """Detections, clustered into one record per real-world object.

    The detector fires several times a second while a camera is over the
    accident, and from three cameras at once. Reporting each frame would bury
    the operator, so nearby sightings merge into one record: position averaged
    over the reliable estimates, and the single best-evidence frame kept.

    Clustering is by ground position, not by time or by camera - which is why
    two drones seeing the same wreck produce one report, and why a second
    wreck elsewhere would produce a second.
    """

    CLUSTER_M = 45.0

    def __init__(self, session_dir):
        self.lock = threading.Lock()
        self.items = []
        self.next_id = 1
        self.dir = os.path.join(session_dir, "detections") if session_dir else None
        if self.dir:
            try:
                os.makedirs(self.dir, exist_ok=True)
            except OSError:
                self.dir = None

    def add(self, sysid, hit, pose, fix, rgb):
        """Record one sighting. `fix` is the projection result from detect.py."""
        now = time.time()
        lat = lon = None
        if fix.get("reliable") and pose.get("lat") is not None:
            lat, lon = mission_mod.offset_to_latlon(
                pose["lat"], pose["lon"], fix["north"], fix["east"])

        with self.lock:
            found = None
            if lat is not None:
                for it in self.items:
                    if it["lat"] is None:
                        continue
                    dn, de = ned_offset(it["lat"], it["lon"], lat, lon)
                    if math.hypot(dn, de) <= self.CLUSTER_M:
                        found = it
                        break
            if found is None and lat is None:
                # An unrangeable sighting only merges into an existing record;
                # on its own it is a bearing, not a place.
                found = next((it for it in self.items
                              if now - it["last_seen"] < 20.0), None)
                if found is None:
                    return None

            if found is None:
                found = {
                    "id": self.next_id, "lat": lat, "lon": lon,
                    "first_seen": now, "last_seen": now, "sightings": 0,
                    "by": [], "best_area": 0.0, "evidence": None,
                    "n_fixes": 0, "sum_w": 0.0, "sum_wlat": 0.0, "sum_wlon": 0.0,
                }
                self.next_id += 1
                self.items.append(found)

            found["last_seen"] = now
            found["sightings"] += 1
            if sysid not in found["by"]:
                found["by"].append(sysid)
            if lat is not None:
                # Weight each fix by how much red it saw. A plain mean lets a
                # distant, near-horizon glimpse count as much as a close
                # overhead look, and those are exactly the fixes whose range
                # is least trustworthy - measured, that cost about 1.5 m of
                # accuracy over a full survey.
                w = max(hit["area_px"], 1.0)
                found["n_fixes"] += 1
                found["sum_w"] += w
                found["sum_wlat"] += lat * w
                found["sum_wlon"] += lon * w
                found["lat"] = found["sum_wlat"] / found["sum_w"]
                found["lon"] = found["sum_wlon"] / found["sum_w"]

            # Keep the frame with the most red in it: the closest, clearest look.
            better = hit["area_px"] > found["best_area"]
            if better:
                found["best_area"] = hit["area_px"]
                found["range_m"] = fix.get("range_m")
                found["bearing_deg"] = fix.get("bearing_deg")
                found["from_alt"] = pose.get("alt")
                found["from_sysid"] = sysid
            item_id = found["id"]

        if better and self.dir is not None:
            try:
                label = (f"drone {sysid}  {hit['area_px']:.0f}px  "
                         f"{time.strftime('%H:%M:%S')}")
                img = detect_mod.annotate(rgb, hit, label)
                import cv2
                cv2.imwrite(os.path.join(self.dir, f"{item_id}.jpg"), img,
                            [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            except Exception:
                pass          # evidence is a bonus, never a reason to fail
        return item_id

    def status(self):
        with self.lock:
            out = []
            for it in sorted(self.items, key=lambda x: x["id"]):
                out.append({
                    "id": it["id"], "lat": it["lat"], "lon": it["lon"],
                    "sightings": it["sightings"], "by": list(it["by"]),
                    "range_m": it.get("range_m"),
                    "bearing_deg": it.get("bearing_deg"),
                    "from_alt": it.get("from_alt"),
                    "from_sysid": it.get("from_sysid"),
                    "area_px": round(it["best_area"]),
                    "age": round(time.time() - it["last_seen"], 1),
                    "fixes": it["n_fixes"],
                })
            return out


def ned_offset(ref_lat, ref_lon, lat, lon):
    """Metres north/east from the reference point to this one."""
    dn = (lat - ref_lat) * 111320.0
    de = (lon - ref_lon) * 111320.0 * math.cos(math.radians(ref_lat))
    return dn, de


class Swarm:
    def __init__(self, count, allow_control=True, allow_video=True,
                 detect=True):
        self.states = []
        for i in range(count):
            st = VehicleState(i, 14552 + 10 * i)
            self.states.append(st)
            threading.Thread(target=reader, args=(st,), daemon=True).start()
        self.history = []  # [{t, errors: {sysid: metres}}]
        self.started = time.time()
        threading.Thread(target=self._sample_history, daemon=True).start()
        self.pilot = control_mod.Pilot(self, enabled=allow_control)
        self.video = video_mod.VideoHub(count) if allow_video else None
        self.scene = mission_mod.load_scene()
        self.sightings = Sightings(session_dir())
        if self.video and detect:
            video_mod.attach_detector(self.video, self._on_frame)

    def _on_frame(self, index, rgb):
        """Detector callback: is the accident in this frame, and where?"""
        hit = detect_mod.find_red(rgb)
        if hit is None:
            return
        st = self.states[index] if index < len(self.states) else None
        if st is None:
            return
        with st.lock:
            pose = {"lat": st.lat, "lon": st.lon, "alt": st.alt,
                    "yaw": st.yaw, "pitch": st.pitch, "roll": st.roll,
                    "sysid": st.sysid}
        if pose["alt"] is None or pose["yaw"] is None:
            return
        h, w = rgb.shape[0], rgb.shape[1]
        fix = detect_mod.project_to_ground(
            hit["cx"], hit["cy"], w, h, pose["alt"], pose["yaw"],
            pose["pitch"] or 0.0, pose["roll"] or 0.0)
        self.sightings.add(pose["sysid"], hit, pose, fix, rgb)

    def _sample_history(self):
        while True:
            time.sleep(1.0)
            snap = self.status()
            errs = {str(f["sysid"]): f["error_m"]
                    for f in snap["followers"] if f["error_m"] is not None}
            if errs:
                self.history.append({"t": round(time.time() - self.started, 1),
                                     "errors": errs})
                cutoff = time.time() - self.started - HISTORY_SECONDS
                self.history = [h for h in self.history if h["t"] >= cutoff]

    def relay_alive(self):
        try:
            with open(RELAY_PIDFILE) as fh:
                pid = int(fh.read().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, FileNotFoundError):
            return False

    def status(self):
        vehicles = [s.snapshot() for s in self.states]
        online = [v for v in vehicles if v["online"]]

        # The leader is whichever online vehicle the followers point at, else
        # the lowest system ID. Derived, not assumed, so a re-pointed
        # FOLL_SYSID shows up here instead of quietly misreporting.
        claimed = {int(v["follow"].get("FOLL_SYSID", 0))
                   for v in online
                   if v["follow"].get("FOLL_ENABLE", 0) >= 1
                   and int(v["follow"].get("FOLL_SYSID", 0)) != v["sysid"]}
        claimed.discard(0)
        leader_sysid = None
        if len(claimed) == 1:
            leader_sysid = claimed.pop()
        elif online:
            leader_sysid = min(v["sysid"] for v in online if v["sysid"])

        leader = next((v for v in vehicles if v["sysid"] == leader_sysid), None)
        followers = []
        for v in vehicles:
            if not v["sysid"] or v["sysid"] == leader_sysid:
                continue
            entry = dict(v)
            entry["offset_n"] = entry["offset_e"] = None
            entry["target_n"] = entry["target_e"] = None
            entry["error_m"] = None
            entry["error_z"] = None
            entry["following"] = bool(v["follow"].get("FOLL_ENABLE", 0) >= 1)
            if leader and leader["lat"] is not None and v["lat"] is not None:
                dn, de = ned_offset(leader["lat"], leader["lon"], v["lat"], v["lon"])
                entry["offset_n"], entry["offset_e"] = round(dn, 2), round(de, 2)
                if entry["following"]:
                    tn = v["follow"].get("FOLL_OFS_X", 0.0)
                    te = v["follow"].get("FOLL_OFS_Y", 0.0)
                    entry["target_n"], entry["target_e"] = round(tn, 2), round(te, 2)
                    # error_m stays horizontal: it is what the top-down plot
                    # and the error chart show. Height is reported separately
                    # rather than folded in, because a follower sitting on the
                    # ground directly under its station would otherwise read
                    # as near-perfect.
                    entry["error_m"] = round(math.hypot(dn - tn, de - te), 2)
                    if leader["alt"] is not None and v["alt"] is not None:
                        tz = -v["follow"].get("FOLL_OFS_Z", 0.0)
                        entry["error_z"] = round(v["alt"] - leader["alt"] - tz, 2)
            followers.append(entry)

        return {
            "now": time.time(),
            "uptime": round(time.time() - self.started, 1),
            "relay_alive": self.relay_alive(),
            "leader_sysid": leader_sysid,
            "leader": leader,
            "followers": followers,
            "vehicles": vehicles,
            "online_count": len(online),
            "expected_count": len(self.states),
            "history": self.history[-HISTORY_SECONDS:],
            "control": self.pilot.state(),
            "detections": self.sightings.status(),
            "scene": self.scene,
            "video": self.video.status() if self.video else
                     {"available": False, "error": "disabled (--no-video)",
                      "cameras": []},
        }


class Handler(BaseHTTPRequestHandler):
    swarm = None

    def log_message(self, *args):
        pass  # keep the console readable

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _stream_video(self, index):
        """Serve one camera as multipart/x-mixed-replace, until the client goes."""
        hub = self.swarm.video
        cam = hub.get(index) if hub else None
        if cam is None:
            self._json(404, {"error": f"no camera {index}",
                             "detail": hub.error if hub else "video disabled"})
            return
        # A viewer that vanishes without closing the connection (a killed
        # browser, a lost network) leaves this thread writing into a socket
        # buffer that nobody drains. A small send buffer plus a send timeout
        # turns that into an exception in seconds instead of minutes, so the
        # thread and its socket are reclaimed. Without this, reloading the
        # page repeatedly accumulates threads for as long as the server runs.
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
            self.connection.settimeout(10.0)
        except OSError:
            pass
        self.send_response(200)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={video_mod.BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            video_mod.stream_mjpeg(cam, self.wfile)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            pass        # the viewer closed the tab; entirely normal

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """The only write path in this server. See dashboard/control.py."""
        if self.path.split("?")[0] != "/api/control":
            self._send(404, b"not found", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": f"bad request body: {exc}"})
            return

        pilot = self.swarm.pilot
        action = body.get("action")
        try:
            if action == "take":
                result = pilot.take()
            elif action == "release":
                result = pilot.release()
            elif action == "move":
                v = body.get("vel") or [0, 0, 0]
                result = pilot.move(v[0], v[1], v[2],
                                    body.get("yaw_rate", 0.0),
                                    body.get("keys"))
            elif action == "takeoff":
                result = pilot.takeoff(float(body.get("alt", 20.0)))
            elif action == "land":
                result = pilot.simple_mode("LAND")
            elif action == "rtl":
                result = pilot.simple_mode("RTL")
            elif action == "formation":
                result = pilot.formation(bool(body.get("on", True)))
            elif action == "goto":
                result = pilot.goto(float(body["lat"]), float(body["lon"]),
                                    body.get("alt"))
            elif action == "mission":
                result = pilot.start_mission(body.get("survey", "highway"))
            elif action == "stop_nav":
                result = pilot.stop_nav()
            elif action == "watch":
                # Accept either a detection id or an explicit position.
                if "detection" in body:
                    hit = next((d for d in self.swarm.sightings.status()
                                if d["id"] == int(body["detection"])
                                and d["lat"] is not None), None)
                    if hit is None:
                        self._json(400, {"error": "no such located detection"})
                        return
                    result = pilot.watch_point(hit["lat"], hit["lon"],
                                               f"accident #{hit['id']}")
                else:
                    result = pilot.watch_point(float(body["lat"]),
                                               float(body["lon"]),
                                               body.get("label"))
            elif action == "unwatch":
                result = pilot.stop_watch()
            else:
                self._json(400, {"error": f"unknown action {action!r}"})
                return
        except Exception as exc:                        # never take the server down
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(200, result)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/video/"):
            try:
                index = int(path.rsplit("/", 1)[1])
            except ValueError:
                self._send(404, b"not found", "text/plain")
                return
            self._stream_video(index)
            return
        if path.startswith("/api/detection/"):
            name = os.path.basename(path)
            d = self.swarm.sightings.dir
            fp = os.path.join(d, name) if d else None
            if not fp or not os.path.isfile(fp) or not name.endswith(".jpg"):
                self._send(404, b"no such evidence frame", "text/plain")
                return
            with open(fp, "rb") as fh:
                self._send(200, fh.read(), "image/jpeg")
            return
        if path == "/api/status":
            body = json.dumps(self.swarm.status()).encode()
            self._send(200, body, "application/json")
            return
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"index.html missing", "text/plain")
            return
        self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=2)
    ap.add_argument("--http-port", type=int,
                    default=int(os.environ.get("DASHBOARD_HTTP_PORT", 8760)))
    ap.add_argument("--no-control", action="store_true",
                    help="serve status only; refuse to command any vehicle")
    ap.add_argument("--no-video", action="store_true",
                    help="do not subscribe to the Gazebo camera topics")
    ap.add_argument("--no-detect", action="store_true",
                    help="stream the cameras but do not look for the accident")
    args = ap.parse_args()

    Handler.swarm = Swarm(args.vehicles, allow_control=not args.no_control,
                          allow_video=not args.no_video,
                          detect=not args.no_detect)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.http_port), Handler)
    except OSError as exc:
        print(f"ERROR: cannot bind port {args.http_port}: {exc}")
        print("       Another dashboard is already running there - and it may be")
        print("       serving an older build of this file, which looks exactly")
        print("       like your edits having no effect.")
        print("       Fix: ./scripts/stop_swarm.sh   (or --http-port 8761)")
        return 1
    print(f"Swarm dashboard on http://127.0.0.1:{args.http_port}")
    print(f"  watching {args.vehicles} vehicle(s) on ports "
          f"{[14552 + 10 * i for i in range(args.vehicles)]}")
    if args.no_control:
        print("  manual control DISABLED (--no-control)")
    else:
        print("  manual control armed but NOT held - the page must take it first")
    if args.no_video:
        print("  video disabled (--no-video)")
    elif video_mod.video_available():
        hub = Handler.swarm.video
        print(f"  video on {', '.join(c.topic for c in hub.cameras.values())}")
    else:
        print(f"  video unavailable: {video_mod.IMPORT_ERROR}")
    print("  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
