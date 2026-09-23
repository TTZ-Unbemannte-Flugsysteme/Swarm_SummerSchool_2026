#!/usr/bin/env bash
# Shared paths, port maths and process handling for every script in this repo.
# Override any of these in your shell before sourcing if your layout differs.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Where ArduPilot and the Gazebo plugin live. Three sources, most specific
# first, so a clone on a fresh machine needs no edits to this file:
#
#   1. exported in your shell   - always wins
#   2. config/local.env         - written by ./install.sh, gitignored
#   3. autodetected below       - the usual places a checkout ends up
#
# local.env uses `: "${VAR:=...}"` so it sets defaults without overriding a
# variable you exported yourself.
[ -f "$REPO/config/local.env" ] && source "$REPO/config/local.env"

if [ -z "${SSS_ROOT:-}" ]; then
  for _cand in "$REPO/deps" "$REPO/../SSS_2026" "$HOME/SSS_2026" "$HOME/swarm-deps"; do
    if [ -d "$_cand/ardupilot" ]; then SSS_ROOT="$(cd "$_cand" && pwd)"; break; fi
  done
  unset _cand
fi
: "${SSS_ROOT:=$REPO/deps}"
: "${ARDUPILOT:=$SSS_ROOT/ardupilot}"
: "${ARDUPILOT_GAZEBO:=$SSS_ROOT/ardupilot_gazebo}"
: "${MISSION_PLANNER:=$SSS_ROOT/MissionPlanner}"

# QGroundControl ships as a single AppImage, so there is no install directory
# to point at - just a file, wherever it was downloaded to.
if [ -z "${QGROUNDCONTROL:-}" ]; then
  for _q in "$HOME"/QGroundControl*.AppImage "$SSS_ROOT"/QGroundControl*.AppImage \
            "$HOME"/Applications/QGroundControl*.AppImage \
            /usr/bin/qgroundcontrol /usr/local/bin/qgroundcontrol; do
    if [ -x "$_q" ]; then QGROUNDCONTROL="$_q"; break; fi
  done
  unset _q
fi
RUNDIR="$REPO/.run"
PIDFILE="$RUNDIR/pids"

# Every launch gets its own timestamped directory, so a run never destroys the
# logs of the one before it. flyit exports SWARM_SESSION so every component it
# starts writes into the same place; a script run on its own makes its own.
SESSION_ID="${SWARM_SESSION:-$(date +%Y%m%d-%H%M%S)}"
SESSION_DIR="$RUNDIR/sessions/$SESSION_ID"

init_session() {
  mkdir -p "$SESSION_DIR"
  # .run/latest always points at the newest session, so logs have a stable path
  ln -sfn "sessions/$SESSION_ID" "$RUNDIR/latest"
  echo "$SESSION_ID" > "$SESSION_DIR/session_id"
  {
    echo "session   $SESSION_ID"
    echo "started   $(date -Iseconds)"
    echo "host      $(hostname)"
    echo "ardupilot $ARDUPILOT"
  } > "$SESSION_DIR/session_info.txt"
}

# Instance i gets sysid i+1 (--auto-sysid) and four MAVProxy outputs:
#   relay     14550 + 10i  - companion/swarm_agent/relay.py
#   control   14551 + 10i  - test scripts (leader_follower.py)
#   dashboard 14552 + 10i  - dashboard/server.py
#   gcs       14553 + 10i  - a ground station bound to one specific vehicle
#   log       14554 + 10i  - scripts/flight_log.py
# One endpoint each because a UDP port has exactly one owner: two consumers on
# one port silently steal each other's packets. On the real aircraft
# mavlink-router fans out the same way.
relay_port()     { echo $((14550 + 10 * $1)); }
control_port()   { echo $((14551 + 10 * $1)); }
dashboard_port() { echo $((14552 + 10 * $1)); }
gcs_port()       { echo $((14553 + 10 * $1)); }
log_port()       { echo $((14554 + 10 * $1)); }
# One shared endpoint that every vehicle also forwards to. A ground station
# attached here sees all N drones as separate vehicles on a single link, which
# is what you want for a swarm - the per-vehicle gcs ports above would need one
# link each.
#
# Deliberately below every per-drone block (14550+10i .. 14554+10i) so it can
# never collide as the drone count grows. It is NOT 14550, which is where
# QGroundControl auto-connects: that port already belongs to relay.py for
# drone 0, and two programs cannot bind one UDP port. Adding the link by hand
# once is the price of keeping the relay where every other page says it is.
QGC_PORT="${QGC_PORT:-14540}"

DASHBOARD_HTTP_PORT="${DASHBOARD_HTTP_PORT:-8760}"

# gz-transport discovers peers by multicast, and picks an interface to do it
# on. If the host has a down interface (a disconnected ethernet port, an idle
# docker0) it can choose that one and log "Exception sending a multicast
# message: Network is unreachable" - after which the camera topics are still
# advertised but no subscriber ever receives a frame. Everything here is on one
# machine, so pin discovery to loopback and stop depending on the host's LAN.
export GZ_IP="${GZ_IP:-127.0.0.1}"

require_ardupilot() {
  if [ ! -x "$ARDUPILOT/build/sitl/bin/arducopter" ]; then
    echo "ERROR: no SITL binary at $ARDUPILOT/build/sitl/bin/arducopter" >&2
    if [ ! -d "$ARDUPILOT" ]; then
      echo "       ArduPilot is not at that path at all (SSS_ROOT=$SSS_ROOT)." >&2
      echo "       Set it up with:  ./install.sh" >&2
      echo "       Already have a checkout? export SSS_ROOT=/path/to/its/parent" >&2
    else
      echo "       Build it with: cd $ARDUPILOT && ./waf configure --board sitl && ./waf copter" >&2
    fi
    echo "       Check the whole rig with: ./scripts/check_setup.sh" >&2
    return 1
  fi
}

record_pid() {
  mkdir -p "$RUNDIR"
  echo "$1" >> "$PIDFILE"
}

# Every process between us and init. Anything in here is a shell or wrapper we
# are running inside, so it must never be counted as a simulation process and
# must never be killed - even though its command line may well contain the
# pattern we are searching for.
ancestors() {
  local pid=$$
  while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    echo "$pid"
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
  done
}

is_ancestor() {
  local target="$1" a
  for a in $(ancestors); do
    [ "$a" = "$target" ] && return 0
  done
  return 1
}

# Count matching processes WITHOUT counting the shell doing the asking.
# A pattern like 'arducopter' appears in the command line of the very command
# checking for it, so a naive pgrep always reports at least one phantom.
count_procs() {
  local pattern="$1" n=0 pid
  for pid in $(pgrep -f "$pattern" 2>/dev/null); do
    is_ancestor "$pid" && continue
    n=$((n + 1))
  done
  echo "$n"
}

running_vehicles() { count_procs 'build/sitl/bin/arducopter'; }

# Stop everything this repo started, and do not return until it is gone.
#
# Leftover SITL instances are worse than useless: a second copy of sysid 1
# binds the same ports, so a test commands one vehicle while reading position
# from another. That failure looks exactly like broken FOLLOW.
#
# Kills by recorded PID first. Pattern matching is a fallback only, with
# patterns anchored on file paths, and never on a bare word - a plain
# 'pkill -f relay.py' will cheerfully kill the interactive shell you typed it
# into, because that shell's own command line contains the pattern.
kill_sim() {
  local pid pattern
  if [ -f "$PIDFILE" ]; then
    while read -r pid; do
      [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null
    done < "$PIDFILE"
    sleep 2
    while read -r pid; do
      [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null
    done < "$PIDFILE"
    rm -f "$PIDFILE"
  fi

  for pattern in 'build/sitl/bin/arducopter' 'autotest/sim_vehicle.py' \
                 'bin/mavproxy.py' 'swarm_agent/relay.py' \
                 'dashboard/server.py' 'scripts/flight_log.py'; do
    for pid in $(pgrep -f "$pattern" 2>/dev/null); do
      is_ancestor "$pid" && continue
      kill -KILL "$pid" 2>/dev/null
    done
  done
  sleep 2

  local left
  left=$(running_vehicles)
  if [ "$left" != "0" ]; then
    echo "WARNING: $left SITL process(es) survived cleanup." >&2
    echo "         Do not trust test results until they are gone." >&2
    return 1
  fi
  return 0
}
