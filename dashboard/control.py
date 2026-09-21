#!/usr/bin/env python3
"""Manual control of the leader from the dashboard.

This is the first write path in what used to be a read-only dashboard, so the
rules it enforces matter more than the code that enforces them:

  * Nothing moves until an operator explicitly takes control. There is no
    live-by-default key handling.
  * A velocity command expires. ArduPilot drops a GUIDED setpoint after about
    three seconds on its own, and this module is stricter - it commands zero
    after DEADMAN_S of silence from the browser. A closed tab, a lost network
    or a wedged page therefore ends in a hover rather than in a vehicle still
    carrying out the last thing it was told.
  * Commands go out as GUIDED velocity setpoints, never as
    RC_CHANNELS_OVERRIDE. An override fights a real transmitter for the same
    channels; a mode-based setpoint is beaten the moment a safety pilot flips
    the mode switch. Only one of those is the right default on real aircraft.

Velocities are in the NED frame - W is north, not "forward" - to match
FOLL_OFS_TYPE=0, which is what the aircraft must use while they have no
compass. The formation does not rotate with the leader's heading, so neither
does the operator's frame of reference.
"""
import math
import threading
import time

from pymavlink import mavutil

import mission as mission_mod

# Send velocity setpoints faster than ArduPilot expires them (~3 s), and give
# up on the browser well before that, so the vehicle is always either under
# active command or stopping.
SEND_HZ = 5.0
DEADMAN_S = 0.5

# Velocity + yaw-rate only: ignore the position, acceleration and yaw-angle
# fields of SET_POSITION_TARGET_LOCAL_NED.
IGNORE_POS = 1 | 2 | 4
IGNORE_ACC = 64 | 128 | 256
IGNORE_YAW = 1024
TYPE_MASK_VELOCITY = IGNORE_POS | IGNORE_ACC | IGNORE_YAW

# Position only: ignore the velocity, acceleration and yaw fields instead.
IGNORE_VEL = 8 | 16 | 32
IGNORE_YAW_RATE = 2048
TYPE_MASK_POSITION = IGNORE_VEL | IGNORE_ACC | IGNORE_YAW | IGNORE_YAW_RATE

# Position, velocity AND yaw: the same target, with the yaw field honoured and
# the leader's velocity passed as feed-forward. The feed-forward is not
# optional. A position-only target re-sent at a few hertz makes the follower
# fly *to* a point and decelerate into it, so chasing a moving leader leaves a
# steady-state lag proportional to speed - measured at roughly 25 m behind a
# leader doing 10 m/s. ArduPilot's own FOLLOW avoids that by feeding the
# leader's velocity into the controller, and so does this.
TYPE_MASK_POSITION_VEL_YAW = IGNORE_ACC | IGNORE_YAW_RATE
TYPE_MASK_POSITION_YAW = IGNORE_VEL | IGNORE_ACC | IGNORE_YAW_RATE
TYPE_MASK_VELOCITY_YAW = IGNORE_POS | IGNORE_ACC | IGNORE_YAW_RATE

# How often the watch loop re-asserts each follower's position and heading.
WATCH_HZ = 3.0

# mode_mapping_acm is number -> name, so invert it once for name -> number.
MODE_NUM = {name: num for num, name in mavutil.mode_mapping_acm.items()}


def _bearing(lat1, lon1, lat2, lon2):
    """Compass bearing and distance from one position to another."""
    dn = (lat2 - lat1) * 111320.0
    de = (lon2 - lon1) * 111320.0 * math.cos(math.radians(lat1))
    return math.degrees(math.atan2(de, dn)) % 360.0, math.hypot(dn, de)

# The follower parameters the formation button writes. Deliberately the same
# values scripts/leader_follower.py uses, so the button and the measured
# evaluation cannot drift apart.
FOLLOW_PARAMS = {
    "FOLL_ENABLE": 1, "FOLL_SYSID": 1, "FOLL_OFS_TYPE": 0, "FOLL_ALT_TYPE": 1,
    "FOLL_YAW_BEHAVE": 0, "FOLL_DIST_MAX": 1000, "FOLL_POS_P": 0.1,
    "FOLL_TIMEOUT": 3.0,
}
STATIONS = [(-25.0, -20.0), (-25.0, 20.0), (-50.0, 0.0), (-50.0, -40.0)]

ACK_NAMES = {0: "accepted", 1: "temporarily rejected", 2: "denied",
             3: "unsupported", 4: "failed", 5: "in progress", 6: "cancelled"}
# 21196 is ArduPilot's "I mean it" magic number for a disarm that would
# otherwise be refused.
FORCE_DISARM = 21196

# How long to keep retrying the arm before giving up and reporting why.
ARM_RETRY_S = 40.0


class Pilot:
    """Holds manual authority over one vehicle and streams setpoints to it.

    `swarm` is the dashboard's Swarm; the leader is looked up on every command
    rather than cached, because which vehicle is the leader is derived from
    live parameters and can change under us.
    """

    def __init__(self, swarm, enabled=True):
        self.swarm = swarm
        self.enabled = enabled
        self.lock = threading.Lock()
        self.held = False            # does an operator currently have control?
        self.vel = (0.0, 0.0, 0.0)   # north, east, down (m/s)
        self.yaw_rate = 0.0          # rad/s, positive = clockwise
        self.last_input = 0.0        # when the browser last said anything
        self.stopping = False        # deadman fired; commanding zero
        self.last_error = None
        self.keys = []               # echoed back to the UI for display
        self._leader_cache = None    # (when, VehicleState) - see _leader()
        self.last_takeoff = {}       # sysid -> {arm, takeoff} ack names
        self.nav = None              # (lat, lon, alt) the leader is flying to
        self.mission = None          # a mission_mod.Mission, if one is running
        # Watching is orthogonal to who is commanding the leader: the leader
        # keeps flying manually, to a target or along a survey, and every
        # camera keeps looking at the same spot on the ground regardless.
        self.watch = None            # (lat, lon, label)
        self.pointing = {}           # sysid -> {yaw, want, off}
        threading.Thread(target=self._fly_mission, daemon=True).start()
        threading.Thread(target=self._watch_loop, daemon=True).start()
        threading.Thread(target=self._stream, daemon=True).start()

    # -- plumbing ----------------------------------------------------------
    def _leader(self, max_age=1.0):
        """The leader's VehicleState, cached briefly.

        Deriving the leader means reading every vehicle's parameters, which is
        far too much work to redo at the setpoint rate - but it must not be
        cached forever either, since a re-pointed FOLL_SYSID legitimately
        changes the answer.
        """
        now = time.time()
        if self._leader_cache and now - self._leader_cache[0] < max_age:
            return self._leader_cache[1]
        sysid = self.swarm.status().get("leader_sysid")
        found = next((st for st in self.swarm.states if st.sysid == sysid), None)
        self._leader_cache = (now, found)
        return found

    def _send(self, state, what, *args, **kwargs):
        """Serialise sends; the reader thread owns the same socket."""
        if state is None or state.conn is None:
            self.last_error = "no leader on the link"
            return False
        try:
            with state.send_lock:
                getattr(state.conn.mav, what)(*args, **kwargs)
            return True
        except Exception as exc:                     # a dead socket, usually
            self.last_error = f"{what}: {exc}"
            return False

    def _set_mode(self, state, mode):
        num = MODE_NUM.get(mode)
        if num is None:
            self.last_error = f"unknown mode {mode}"
            return False
        return self._send(state, "set_mode_send", state.conn.target_system,
                          mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, num)

    def _watch_yaw(self, state):
        """Bearing from this vehicle to the watched point, or None."""
        with self.lock:
            w = self.watch
        if w is None or state is None or state.lat is None:
            return None
        return _bearing(state.lat, state.lon, w[0], w[1])[0]

    def _velocity(self, state, vn, ve, vd, yaw_rate):
        # While a point is being watched, the aim rides along in the same
        # message. Sending yaw separately does not work: every new velocity or
        # position target resets GUIDED's yaw to "face the direction of
        # travel", so a standalone CONDITION_YAW is overwritten as fast as it
        # is sent - and the camera swings away exactly while the vehicle moves.
        aim = self._watch_yaw(state)
        if aim is not None:
            return self._send(state, "set_position_target_local_ned_send",
                              0, state.conn.target_system,
                              state.conn.target_component,
                              mavutil.mavlink.MAV_FRAME_LOCAL_NED,
                              TYPE_MASK_VELOCITY_YAW,
                              0, 0, 0, vn, ve, vd, 0, 0, 0,
                              math.radians(aim), 0)
        return self._send(state, "set_position_target_local_ned_send",
                          0, state.conn.target_system, state.conn.target_component,
                          mavutil.mavlink.MAV_FRAME_LOCAL_NED, TYPE_MASK_VELOCITY,
                          0, 0, 0, vn, ve, vd, 0, 0, 0, 0, yaw_rate)

    def _position_target(self, state, lat, lon, alt, aim=True):
        """Send an absolute position target.

        SET_POSITION_TARGET_GLOBAL_INT carries lat/lon as 1e7 integers.
        MAV_CMD_DO_REPOSITION would be the obvious alternative, but it passes
        them as float32 command parameters, which quantises a position at
        these latitudes to roughly a metre - visible as waypoints the
        formation never quite settles on.
        """
        aim_deg = self._watch_yaw(state) if aim else None
        mask = TYPE_MASK_POSITION_YAW if aim_deg is not None else TYPE_MASK_POSITION
        return self._send(state, "set_position_target_global_int_send",
                          0, state.conn.target_system, state.conn.target_component,
                          mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, mask,
                          int(round(lat * 1e7)), int(round(lon * 1e7)), float(alt),
                          0, 0, 0, 0, 0, 0,
                          math.radians(aim_deg) if aim_deg is not None else 0, 0)

    def _hold(self, state):
        """Park a vehicle where it is, without needing a pilot.

        NOT LOITER. LOITER is a pilot-assisted mode: it holds position
        horizontally but takes its climb rate from the throttle stick, and in
        SITL - and on any aircraft whose transmitter is off or whose throttle
        is down - that stick reads as full descent. Handing a vehicle to
        LOITER therefore lands it, silently and with no failsafe message,
        which is exactly what happened to every survey that "completed" here.

        Holding the current position as a GUIDED target needs no RC input at
        all. Position targets, unlike velocity targets, do not expire, so one
        message is enough.
        """
        if state is None or state.conn is None:
            return False
        if state.lat is None or state.alt is None:
            return self._set_mode(state, "ALT_HOLD")
        self._set_mode(state, "GUIDED")
        return self._position_target(state, state.lat, state.lon,
                                     max(state.alt, 2.0))

    def _command_ack(self, state, cmd, *params, timeout=3.0):
        """Send a command and wait for its COMMAND_ACK.

        Without this, a rejected arm or takeoff produces no visible effect
        whatsoever - the vehicle simply sits there and the UI reports success.
        The acks are captured by the reader thread, which owns the socket.
        """
        state.acks.pop(cmd, None)
        sent = time.time()
        if not self._command(state, cmd, *params):
            return "send failed"
        deadline = sent + timeout
        while time.time() < deadline:
            got = state.acks.get(cmd)
            if got and got[1] >= sent:
                return ACK_NAMES.get(got[0], f"result {got[0]}")
            time.sleep(0.05)
        return "no ack"

    def _command(self, state, cmd, *params):
        p = list(params) + [0.0] * (7 - len(params))
        return self._send(state, "command_long_send", state.conn.target_system,
                          state.conn.target_component, cmd, 0, *p)

    # -- the streaming loop ------------------------------------------------
    def _stream(self):
        """Repeat the current setpoint, and zero it when the browser goes quiet.

        Repetition is not an optimisation: a GUIDED setpoint sent once expires,
        so a held key has to be re-asserted. The same loop is what makes the
        deadman work, because it is already running when the browser stops
        talking.
        """
        while True:
            time.sleep(1.0 / SEND_HZ)
            with self.lock:
                if not self.held:
                    continue
                if time.time() - self.last_input > DEADMAN_S:
                    # The browser has gone quiet. Whatever the reason, the
                    # answer is the same: stop asking for movement.
                    if any(self.vel) or self.yaw_rate:
                        self.stopping = True
                    self.vel, self.yaw_rate, self.keys = (0.0, 0.0, 0.0), 0.0, []
                vn, ve, vd = self.vel
                yaw = self.yaw_rate
            state = self._leader()
            if state is None or state.conn is None:
                self.last_error = "no leader on the link"
                continue
            self._velocity(state, vn, ve, vd, yaw)

    # -- one intent at a time ----------------------------------------------
    def _clear_intent(self, keep=None, why=None):
        """Drop every command source except `keep`.

        Manual control, a goto and a survey each send setpoints to the leader
        several times a second. Two of them running at once does not blend -
        it alternates, and the vehicle does neither thing. Every entry point
        therefore claims sole ownership before it starts.
        """
        with self.lock:
            if keep != "manual":
                self.held = False
                self.vel, self.yaw_rate, self.keys = (0.0, 0.0, 0.0), 0.0, []
            if keep != "goto":
                self.nav = None
            if keep != "mission" and self.mission is not None:
                if self.mission.finished is None:
                    self.mission.aborted = True
                    self.mission.finished = time.time()
                    self.mission.note = why or "superseded"
        # A finished survey stays on screen as a record; _fly_mission only
        # ever acts on one whose `finished` is still None.

    # -- actions the browser can ask for -----------------------------------
    def take(self):
        if not self.enabled:
            return self.state("manual control is disabled (--no-control)")
        state = self._leader()
        if state is None:
            return self.state("no leader on the link")
        self._clear_intent(keep="manual", why="pre-empted by manual control")
        self._set_mode(state, "GUIDED")
        with self.lock:
            self.held = True
            self.vel, self.yaw_rate = (0.0, 0.0, 0.0), 0.0
            self.last_input = time.time()
            self.stopping = False
            self.last_error = None
        return self.state()

    def release(self):
        state = self._leader()
        self._clear_intent(why="released")
        # Stop, then hand the vehicle to a mode that holds position without
        # needing anyone to keep talking to it.
        if state is not None and state.conn is not None:
            self._velocity(state, 0, 0, 0, 0)
            self._hold(state)
        return self.state()

    def move(self, vn, ve, vd, yaw_rate, keys=None):
        with self.lock:
            if not self.held:
                return self.state("take control first")
            self.vel = (float(vn), float(ve), float(vd))
            self.yaw_rate = float(yaw_rate)
            self.last_input = time.time()
            self.stopping = False
            self.keys = list(keys or [])
        return self.state()

    def goto(self, lat, lon, alt=None):
        """Fly the leader to an absolute position - one click on the map.

        This deliberately drops manual velocity control. Leaving the keyboard
        loop running would have it fight the position target at 5 Hz, and the
        vehicle would sit between the two doing neither.
        """
        state = self._leader()
        if state is None or state.conn is None:
            return self.state("no leader on the link")
        if alt is None:
            alt = state.alt if state.alt and state.alt > 2.0 else 20.0
        if not state.armed:
            return self.state("leader is not armed - take off first")
        self._clear_intent(keep="goto", why="superseded by a map target")
        with self.lock:
            self.nav = (float(lat), float(lon), float(alt))
        self._set_mode(state, "GUIDED")
        self._position_target(state, lat, lon, alt)
        return self.state()

    def stop_nav(self):
        """Cancel a goto or a mission and hold position."""
        self._clear_intent(why="aborted by the operator")
        state = self._leader()
        if state is not None and state.conn is not None:
            self._hold(state)
        return self.state()

    # -- pointing every camera at one spot ---------------------------------
    def watch_point(self, lat, lon, label=None):
        """Aim every drone's camera at one ground position.

        The cameras are bolted to the airframes - no gimbals, matching the real
        F450s - so aiming a camera means yawing the aircraft. That is free
        here: FOLL_OFS_TYPE=0 puts the formation offsets in the NED frame, so
        the geometry does not rotate with heading and yaw is an unused degree
        of freedom.

        What it does cost is FOLLOW mode. ArduPilot's FOLLOW owns the yaw
        controller and silently ignores MAV_CMD_CONDITION_YAW - it acks the
        command and does nothing - and FOLL_YAW_BEHAVE only offers "face the
        leader", "copy the leader" and "face the direction of flight", none of
        which is "face that point over there". Copying the leader's heading is
        a usable approximation at a distance, but it fails exactly when it
        matters: with the swarm close to the wreck the per-drone bearings
        diverge by more than the lens can cover.

        So while watching, the followers move to GUIDED and this loop holds
        their stations from the ground instead - Option B from the
        architecture document, for as long as the cameras are aimed. Their
        FOLL_* parameters are left untouched so the handback is exact.
        """
        state = self._leader()
        if state is None or state.conn is None:
            return self.state("no leader on the link")
        followers = [st for st in self.swarm.states
                     if st.sysid is not None and st.conn is not None
                     and st is not state]
        for st in followers:
            if not st.follow.get("FOLL_OFS_X") and not st.follow.get("FOLL_OFS_Y"):
                return self.state(f"drone {st.sysid} has no formation offset yet"
                                  " - set the formation first")
            self._set_mode(st, "GUIDED")
        with self.lock:
            self.watch = (float(lat), float(lon), label or "target")
        return self.state()

    def stop_watch(self):
        """Hand the followers back to FOLLOW and stop aiming."""
        with self.lock:
            self.watch = None
            self.pointing = {}
        leader = self._leader()
        for st in self.swarm.states:
            if st.sysid is None or st.conn is None or st is leader:
                continue
            if st.follow.get("FOLL_ENABLE", 0) >= 1:
                self._set_mode(st, "FOLLOW")
        return self.state()

    def _watch_loop(self):
        while True:
            time.sleep(1.0 / WATCH_HZ)
            with self.lock:
                w = self.watch
            if w is None:
                continue
            tlat, tlon, _ = w
            leader = self._leader()
            if leader is None or leader.conn is None or leader.lat is None:
                continue

            pointing = {}
            # The leader keeps whatever is flying it; only its heading is ours.
            br, _ = _bearing(leader.lat, leader.lon, tlat, tlon)
            with self.lock:
                commanded = (self.held or self.nav is not None
                             or (self.mission is not None
                                 and self.mission.finished is None))
            if not commanded:
                # Nothing else is talking to the leader, so a plain yaw
                # command is the only way to turn it.
                self._command(leader, mavutil.mavlink.MAV_CMD_CONDITION_YAW,
                              br, 45.0, 0, 0)
            if leader.yaw is not None:
                pointing[leader.sysid] = {
                    "yaw": round(leader.yaw, 1), "want": round(br, 1),
                    "off": round((leader.yaw - br + 180) % 360 - 180, 1)}

            for st in self.swarm.states:
                if st.sysid is None or st.conn is None or st is leader:
                    continue
                if st.lat is None:
                    continue
                # Station relative to the leader, exactly as FOLLOW would fly
                # it - the same FOLL_OFS values, just applied out here.
                ofs_n = st.follow.get("FOLL_OFS_X", 0.0)
                ofs_e = st.follow.get("FOLL_OFS_Y", 0.0)
                ofs_d = st.follow.get("FOLL_OFS_Z", 0.0)
                slat, slon = mission_mod.offset_to_latlon(
                    leader.lat, leader.lon, ofs_n, ofs_e)
                salt = max((leader.alt or 20.0) - ofs_d, 2.0)
                # Each drone gets its own bearing. This is the whole point:
                # from 40 m apart and close to the target those differ by far
                # more than the 69 degree lens can absorb.
                fbr, _ = _bearing(st.lat, st.lon, tlat, tlon)
                self._send(st, "set_position_target_global_int_send",
                           0, st.conn.target_system, st.conn.target_component,
                           mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                           TYPE_MASK_POSITION_VEL_YAW,
                           int(round(slat * 1e7)), int(round(slon * 1e7)),
                           float(salt),
                           leader.vn or 0.0, leader.ve or 0.0, leader.vd or 0.0,
                           0, 0, 0, math.radians(fbr), 0)
                if st.yaw is not None:
                    pointing[st.sysid] = {
                        "yaw": round(st.yaw, 1), "want": round(fbr, 1),
                        "off": round((st.yaw - fbr + 180) % 360 - 180, 1)}
            with self.lock:
                self.pointing = pointing

    # -- missions ----------------------------------------------------------
    def start_mission(self, kind="highway", **kw):
        state = self._leader()
        if state is None or state.conn is None:
            return self.state("no leader on the link")
        if not state.armed or (state.alt or 0) < 2.0:
            return self.state("take off before starting a survey")
        if state.home_lat is None:
            return self.state("leader home position not known yet")

        scene = mission_mod.load_scene()
        leader_east = 0.0
        for v in (scene or {}).get("vehicles", []):
            if v["index"] == state.index:
                leader_east = v["east"]
        if kind == "highway":
            wps = mission_mod.highway_legs(scene, leader_east, **kw)
        elif kind == "area":
            wps = mission_mod.area_lawnmower(leader_east=leader_east,
                                             scene=scene, **kw)
        else:
            return self.state(f"unknown survey {kind!r}")

        self._clear_intent(why="superseded by a new survey")
        with self.lock:
            self.mission = mission_mod.Mission(kind, wps, state.home_lat,
                                               state.home_lon)
        self._set_mode(state, "GUIDED")
        return self.state()

    def _fly_mission(self):
        """Advance the leader through the waypoints, one at a time.

        Re-sending the current target every cycle rather than once is the same
        reasoning as the velocity loop: a GUIDED target is not a durable
        instruction, and a single dropped packet must not leave the survey
        stalled forever.
        """
        while True:
            time.sleep(1.0)
            with self.lock:
                m = self.mission
                nav = self.nav
            state = self._leader()
            if state is None or state.conn is None:
                continue

            if m is not None and m.finished is None:
                target = m.target_latlon()
                if target is None:
                    with self.lock:
                        m.finished = time.time()
                        m.note = "survey complete - holding position"
                    self._hold(state)
                    continue
                lat, lon, alt = target
                self._position_target(state, lat, lon, alt)
                if state.lat is None:
                    continue
                dn, de = mission_mod.latlon_to_offset(lat, lon, state.lat, state.lon)
                dist = math.hypot(dn, de)
                if dist <= mission_mod.ACCEPT_RADIUS_M:
                    with self.lock:
                        m.index += 1
                        m.leg_started = time.time()
                elif time.time() - m.leg_started > mission_mod.LEG_TIMEOUT_S:
                    with self.lock:
                        m.note = (f"leg {m.index + 1} timed out {dist:.0f} m "
                                  f"short; skipping")
                        m.index += 1
                        m.leg_started = time.time()
                continue

            # A plain goto: keep asserting it until the leader gets there.
            if nav is not None:
                lat, lon, alt = nav
                self._position_target(state, lat, lon, alt)
                if state.lat is not None:
                    dn, de = mission_mod.latlon_to_offset(lat, lon,
                                                          state.lat, state.lon)
                    if math.hypot(dn, de) <= mission_mod.ACCEPT_RADIUS_M:
                        with self.lock:
                            self.nav = None

    def takeoff(self, alt=20.0):
        """Arm and climb *every* vehicle, not just the leader.

        A follower sitting disarmed on the ground in FOLLOW mode reports a
        large station error and looks like a formation failure, so there is no
        useful state in which only the leader is airborne. This is the same
        order scripts/leader_follower.py uses: everyone up in GUIDED first,
        then the followers switch to FOLLOW.
        """
        targets = [st for st in self.swarm.states
                   if st.sysid is not None and st.conn is not None]
        if not targets:
            return self.state("no vehicles on the link")
        # Without this, a leftover goto or survey target keeps arriving during
        # the climb; ArduPilot honours it, and the takeoff stops short.
        self._clear_intent(why="superseded by a take-off")

        # ArduPilot will not arm until the EKF has a position it trusts, which
        # is roughly a minute after the vehicles start. Refusing here, by name,
        # beats three identical "arm: failed" acks a second later.
        unready = [f"drone {st.sysid} ("
                   + ", ".join(
                       ([] if st.ekf_ok else ["EKF not ready"])
                       + ([] if (st.gps_fix or 0) >= 3 else ["no GPS fix"]))
                   + ")"
                   for st in targets
                   if not st.ekf_ok or (st.gps_fix or 0) < 3]
        if unready:
            return self.state("not ready to fly yet: " + "; ".join(unready)
                              + " - this usually clears about a minute after "
                                "the vehicles start")

        # A vehicle that is armed and on the ground will not accept a GUIDED
        # takeoff - which is the state the swarm is left in after the leader
        # lands and the followers descend with it, still armed. Force a disarm
        # first so every vehicle starts from the same place.
        stale = [st for st in targets
                 if st.armed and (st.alt is None or st.alt < 1.0)]
        for st in stale:
            self._command(st, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                          0, FORCE_DISARM)
        if stale:
            time.sleep(2.0)

        results = {}
        for st in targets:
            self._set_mode(st, "GUIDED")
        time.sleep(0.5)

        # Keep asking, rather than asking once. ArduPilot runs dozens of
        # pre-arm checks and several are transient at start-up - "Accels
        # inconsistent" and "EKF3 not ready" both clear on their own within a
        # minute. Trying to predict them from here means reimplementing the
        # autopilot's checklist and getting it wrong on the next firmware; the
        # autopilot already knows, so ask it until it says yes.
        deadline = time.time() + ARM_RETRY_S
        pending = {st.sysid: st for st in targets}
        while pending and time.time() < deadline:
            for sysid, st in list(pending.items()):
                if st.armed:
                    results[sysid] = {"arm": "accepted"}
                    del pending[sysid]
                    continue
                res = self._command_ack(
                    st, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 1)
                results[sysid] = {"arm": res}
                if res == "accepted":
                    del pending[sysid]
            if pending:
                time.sleep(2.0)
        for sysid, st in pending.items():
            results[sysid] = {"arm": results.get(sysid, {}).get("arm", "failed")}

        # An accepted arm is not an armed vehicle yet: the armed flag arrives
        # with the next HEARTBEAT, up to a second later. Sampling it straight
        # away leaves whichever vehicle's heartbeat lands last sitting on the
        # ground while the others climb.
        armed_by = time.time() + 6.0
        while time.time() < armed_by:
            waiting = [st for st in targets
                       if not st.armed
                       and results.get(st.sysid, {}).get("arm") == "accepted"]
            if not waiting:
                break
            time.sleep(0.3)

        for st in targets:
            entry = results.setdefault(st.sysid, {})
            entry["takeoff"] = self._command_ack(
                st, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0, 0, 0, 0, 0, 0, float(alt)) if st.armed else "not armed"

        texts = {st.sysid: st.last_text for st in targets}
        bad = [f"drone {k}: arm {v['arm']}, takeoff {v['takeoff']}"
               + (f" - {texts[k]}" if texts.get(k) else "")
               for k, v in sorted(results.items())
               if v["arm"] != "accepted" or v["takeoff"] != "accepted"]
        with self.lock:
            self.last_takeoff = results
        return self.state("; ".join(bad) if bad else None)

    def simple_mode(self, mode, everyone=True):
        """Hand the swarm to a self-sufficient mode - LAND or RTL.

        Applied to every vehicle by default: landing only the leader leaves
        followers holding station on a vehicle that is already on the ground.
        """
        targets = [st for st in self.swarm.states
                   if st.sysid is not None and st.conn is not None]
        if not everyone:
            leader = self._leader()
            targets = [leader] if leader is not None else []
        if not targets:
            return self.state("no vehicles on the link")
        self._clear_intent(why=f"superseded by {mode}")
        for st in targets:
            self._set_mode(st, mode)
        return self.state()

    def formation(self, on=True):
        if self.watch is not None:
            self.stop_watch()
        return self._formation(on)

    def _formation(self, on=True):
        """Put every follower into FOLLOW at its station, or take them out.

        Manual control of the leader is only interesting if there is a
        formation behind it, and this is the same parameter set the measured
        evaluation uses.
        """
        status = self.swarm.status()
        leader_sysid = status.get("leader_sysid")
        n = 0
        for st in self.swarm.states:
            if st.sysid is None or st.sysid == leader_sysid or st.conn is None:
                continue
            if not on:
                self._set_param(st, "FOLL_ENABLE", 0)
                self._hold(st)
                n += 1
                continue
            ofs_n, ofs_e = STATIONS[(st.sysid - 2) % len(STATIONS)]
            params = dict(FOLLOW_PARAMS)
            params["FOLL_SYSID"] = leader_sysid or 1
            params["FOLL_OFS_X"], params["FOLL_OFS_Y"] = ofs_n, ofs_e
            params["FOLL_OFS_Z"] = 0.0
            for name, value in params.items():
                self._set_param(st, name, value)
            self._set_mode(st, "FOLLOW")
            n += 1
        return self.state(None if n else "no followers on the link")

    def _set_param(self, state, name, value):
        return self._send(state, "param_set_send", state.conn.target_system,
                          state.conn.target_component, name.encode(),
                          float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

    # -- what the browser gets back ----------------------------------------
    def state(self, error=None):
        with self.lock:
            return {
                "enabled": self.enabled,
                "held": self.held,
                "vel": list(self.vel),
                "yaw_rate": self.yaw_rate,
                "keys": list(self.keys),
                "stopping": self.stopping,
                "quiet_for": round(time.time() - self.last_input, 2)
                if self.last_input else None,
                "deadman_s": DEADMAN_S,
                "last_takeoff": dict(self.last_takeoff),
                "nav": list(self.nav) if self.nav else None,
                "watch": ({"lat": self.watch[0], "lon": self.watch[1],
                           "label": self.watch[2]} if self.watch else None),
                "pointing": dict(self.pointing),
                "mission": self.mission.state() if self.mission else None,
                "error": error or self.last_error,
            }
