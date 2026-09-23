#!/usr/bin/env bash
# Launch N ArduCopter SITL instances, each with its own sysid and MAVLink endpoint.
#
#   ./launch_swarm.sh            # 2 vehicles, no Gazebo (fast, headless)
#   ./launch_swarm.sh 3          # 3 vehicles, no Gazebo
#   ./launch_swarm.sh 2 gazebo   # 2 vehicles in the Gazebo world
#
# Vehicle i  ->  sysid i+1  ->  udp 127.0.0.1:(14550 + 10i)
set -u

source "$(dirname "$0")/env.sh"

COUNT="${1:-2}"
MODE="${2:-plain}"

require_ardupilot || exit 1

echo "Cleaning up old simulation processes..."
kill_sim || exit 1

LEFT=$(running_vehicles)
if [ "$LEFT" != "0" ]; then
  echo "ERROR: $LEFT SITL process(es) still running. Refusing to stack more." >&2
  exit 1
fi

init_session
LOGDIR="$SESSION_DIR"
echo "Session $SESSION_ID  ->  .run/sessions/$SESSION_ID"

trap 'echo; echo "Shutting down..."; kill_sim; exit' EXIT SIGINT SIGTERM

if [ "$MODE" = "gazebo" ]; then
  # One cloned model per vehicle, each on its own FDM port.
  # Camera sensors are rendered and the plugin is lock-stepped with SITL, so
  # they can cost real-time factor. SWARM_CAMERAS=0 builds the same world
  # without them, which is how you measure what they cost.
  WORLD_ARGS=()
  [ "${SWARM_CAMERAS:-1}" = "0" ] && WORLD_ARGS+=(--no-cameras)
  [ -n "${SWARM_CAMERA_SIZE:-}" ] && WORLD_ARGS+=(--camera-size "$SWARM_CAMERA_SIZE")
  [ -n "${SWARM_CAMERA_RATE:-}" ] && WORLD_ARGS+=(--camera-rate "$SWARM_CAMERA_RATE")
  # SWARM_ACCIDENT=0 stages no accident: the detector's negative control, which
  # must then report nothing at all over a full survey.
  [ "${SWARM_ACCIDENT:-1}" = "0" ] && WORLD_ARGS+=(--no-accident)
  [ -n "${SWARM_ACCIDENT_NORTH:-}" ] && WORLD_ARGS+=(--accident-north "$SWARM_ACCIDENT_NORTH")
  python3 "$REPO/scripts/make_swarm_world.py" "$COUNT" "${WORLD_ARGS[@]}" || exit 1

  export GZ_SIM_SYSTEM_PLUGIN_PATH="${GZ_SIM_SYSTEM_PLUGIN_PATH:-}:$ARDUPILOT_GAZEBO/build"
  export GZ_SIM_RESOURCE_PATH="${GZ_SIM_RESOURCE_PATH:-}:$ARDUPILOT_GAZEBO/models:$ARDUPILOT_GAZEBO/worlds:$REPO/models:$REPO/worlds"
  WORLD="${GZ_WORLD:-swarm_runway.sdf}"
  GZ_FLAGS="${GZ_FLAGS:--v2 -r}"
  echo "Starting Gazebo world: $WORLD  (gz sim $GZ_FLAGS)"
  gz sim $GZ_FLAGS "$WORLD" > "$LOGDIR/gazebo.log" 2>&1 &
  record_pid $!
  sleep 8
  if ! grep -qi "error\|unable to find" "$LOGDIR/gazebo.log" 2>/dev/null; then
    echo "      Gazebo started."
  else
    echo "      WARNING: Gazebo logged errors - see .run/gazebo.log" >&2
  fi
  FRAME_ARGS=(-f gazebo-iris --model JSON)
  # Gazebo owns the physics, so the model pose in the world IS the vehicle's
  # position. Adding --custom-location on top would offset the home as well and
  # double-count the spacing, so every instance keeps the world's origin.
  USE_CUSTOM_LOCATION=0
else
  FRAME_ARGS=(-f quad)
  USE_CUSTOM_LOCATION=1
fi

cd "$ARDUPILOT/ArduCopter" || exit 1

# Spawn the vehicles spaced along an east-west line so they do not start
# stacked on one point. Base is SITL's default CMAC field.
BASE_LAT=-35.363262
BASE_LON=149.165237
BASE_ALT=584
BASE_HDG=353
SPACING_M=${SPACING_M:-20}

for i in $(seq 0 $((COUNT - 1))); do
  RPORT=$(relay_port "$i")
  CPORT=$(control_port "$i")
  DPORT=$(dashboard_port "$i")
  GPORT=$(gcs_port "$i")
  # Every vehicle also forwards to the one shared ground-station endpoint, so a
  # single QGroundControl link shows the whole swarm instead of one drone.
  LPORT=$(log_port "$i")
  SYSID=$((i + 1))
  LON=$(awk -v b="$BASE_LON" -v i="$i" -v s="$SPACING_M" -v lat="$BASE_LAT" \
        'BEGIN{printf "%.7f", b + i*s/(111320*cos(lat*3.14159265/180))}')
  echo "[vehicle $i] sysid $SYSID -> relay $RPORT control $CPORT dash $DPORT gcs $GPORT log $LPORT"

  LOC_ARGS=()
  if [ "$USE_CUSTOM_LOCATION" = "1" ]; then
    LOC_ARGS=(--custom-location="$BASE_LAT,$LON,$BASE_ALT,$BASE_HDG")
  fi
  # --daemon is not optional: MAVProxy is started backgrounded, so its
  # interactive stdin loop would hit EOF and exit, killing the vehicle.
  # MAVPROXY_EXTRA adds to it, never replaces it.
  #
  # This string must contain no double spaces. sim_vehicle.py splits -m on
  # spaces and then indexes x[0] on every token, so one empty token kills the
  # launch with "IndexError: string index out of range".
  MP_ARGS="--daemon"
  if [ -n "${MAVPROXY_EXTRA:-}" ]; then
    MP_ARGS="$MP_ARGS $MAVPROXY_EXTRA"
  fi

  SITL_RITW_TERMINAL="nohup" ../Tools/autotest/sim_vehicle.py \
    -v ArduCopter "${FRAME_ARGS[@]}" \
    -I "$i" --auto-sysid --no-rebuild \
    --use-dir "$RUNDIR/v$i" \
    "${LOC_ARGS[@]+"${LOC_ARGS[@]}"}" \
    --add-param-file="$REPO/config/sitl/f450.parm" \
    -m "$MP_ARGS --out 127.0.0.1:$RPORT --out 127.0.0.1:$CPORT --out 127.0.0.1:$DPORT --out 127.0.0.1:$GPORT --out 127.0.0.1:$LPORT --out 127.0.0.1:$QGC_PORT" \
    > "$LOGDIR/vehicle$i.log" 2>&1 &
  record_pid $!

  sleep 4
done

echo
echo "Waiting for vehicles to come up (this takes ~20s on first boot)..."
sleep 16
echo
echo "----------------------------------------------------------"
echo "Launched $(running_vehicles) vehicle(s). Verify with:"
echo "    python3 $REPO/scripts/check_vehicles.py $COUNT"
echo
echo "Logs: .run/sessions/$SESSION_ID/  (also reachable as .run/latest/)"
echo "Ctrl+C here shuts everything down."
echo "----------------------------------------------------------"

wait
