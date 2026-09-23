#!/usr/bin/env bash
# check_setup.sh - is this machine able to run the simulation?
#
# Checks every dependency flyit needs, prints one line each, and ends with
# RESULT: PASS or RESULT: FAIL like the rest of the repo. Run it after
# ./install.sh, or any time something behaves strangely - it separates "the
# rig is broken" from "the code is broken", which is most of the debugging.
#
#   ./scripts/check_setup.sh
#
# Some things are optional. A missing optional dependency prints WARN and
# still allows PASS: you lose that feature, not the simulation.
set -u

source "$(dirname "$0")/env.sh"

PASS=0; WARN=0; FAIL=0

ok()   { printf '  \033[32mok  \033[0m %-22s %s\n' "$1" "${2:-}"; PASS=$((PASS+1)); }
warn() { printf '  \033[33mwarn\033[0m %-22s %s\n' "$1" "${2:-}"; WARN=$((WARN+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %-22s %s\n' "$1" "${2:-}"; FAIL=$((FAIL+1)); }
fix()  { printf '       %-22s -> %s\n' "" "$1"; }

# Python package present? Report its version, since a too-old pymavlink is a
# real failure mode and reports itself as "present".
pyver() { python3 -c "import $1, sys; print(getattr($1, '__version__', 'installed'))" 2>/dev/null; }

echo "=============================================="
echo " check_setup - dependencies for this repo"
echo "=============================================="
echo
echo "Paths"
printf '  %-22s %s\n' "repo"       "$REPO"
printf '  %-22s %s\n' "SSS_ROOT"   "$SSS_ROOT"
if [ -f "$REPO/config/local.env" ]; then
  printf '  %-22s %s\n' "config"   "config/local.env"
else
  printf '  %-22s %s\n' "config"   "(none - paths autodetected)"
fi
echo

# --- the host ---------------------------------------------------------------
echo "Host"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04) ok "os" "${PRETTY_NAME:-unknown}" ;;
    *) warn "os" "${PRETTY_NAME:-unknown} (tested on Ubuntu 22.04 / 24.04)" ;;
  esac
else
  warn "os" "unknown"
fi

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
if [ -z "$PYV" ]; then
  bad "python3" "not found"; fix "sudo apt install python3 python3-pip"
elif python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)'; then
  ok "python3" "$PYV"
else
  bad "python3" "$PYV (need 3.8+)"
fi
echo

# --- Python packages --------------------------------------------------------
echo "Python packages"
V=$(pyver pymavlink)
if [ -n "$V" ]; then ok "pymavlink" "$V"
else bad "pymavlink" "missing"; fix "pip3 install --user -r requirements.txt"; fi

if command -v mavproxy.py > /dev/null 2>&1; then
  ok "MAVProxy" "$(command -v mavproxy.py)"
else
  bad "MAVProxy" "mavproxy.py not on PATH"
  fix "pip3 install --user MAVProxy, then add ~/.local/bin to PATH"
fi

V=$(pyver numpy)
if [ -n "$V" ]; then ok "numpy" "$V"
else warn "numpy" "missing - no camera feeds, no accident detection"; fi

V=$(pyver cv2)
if [ -n "$V" ]; then ok "opencv (cv2)" "$V"
else warn "opencv (cv2)" "missing - no camera feeds, no accident detection"; fi
echo

# --- ArduPilot --------------------------------------------------------------
echo "ArduPilot SITL"
if [ -d "$ARDUPILOT" ]; then
  ok "checkout" "$ARDUPILOT"
  if [ -x "$ARDUPILOT/build/sitl/bin/arducopter" ]; then
    # The SITL binary has no --version; the checkout's tag is the honest
    # answer, and it is what the README's version caveats talk about.
    VER=$(git -C "$ARDUPILOT" describe --tags --always 2>/dev/null)
    ok "arducopter binary" "built${VER:+  ($VER)}"
  else
    bad "arducopter binary" "not built"
    fix "cd $ARDUPILOT && ./waf configure --board sitl && ./waf copter"
  fi
  if [ -x "$ARDUPILOT/Tools/autotest/sim_vehicle.py" ]; then
    ok "sim_vehicle.py" "present"
  else
    bad "sim_vehicle.py" "missing - incomplete checkout?"
    fix "cd $ARDUPILOT && git submodule update --init --recursive"
  fi
else
  bad "checkout" "nothing at $ARDUPILOT"
  fix "./install.sh   (or export SSS_ROOT=/path/to/parent)"
fi
echo

# --- Gazebo -----------------------------------------------------------------
echo "Gazebo"
if command -v gz > /dev/null 2>&1; then
  ok "gz" "sim $(gz sim --versions 2>/dev/null | head -1)"
else
  warn "gz" "not found - flyit --no-gazebo still works (headless physics)"
  fix "./install.sh, or see gazebosim.org/docs/harmonic/install_ubuntu"
fi

if [ -f "$ARDUPILOT_GAZEBO/build/libArduPilotPlugin.so" ]; then
  ok "ardupilot_gazebo" "$ARDUPILOT_GAZEBO/build"
else
  warn "ardupilot_gazebo" "plugin not built - no Gazebo mode"
  fix "./install.sh, or build it per ArduPilot/ardupilot_gazebo's README"
fi

# The dashboard's video path needs the gz Python bindings, which pip cannot
# supply. Missing them costs the camera feeds and nothing else, so: warn.
if python3 -c "
import os
os.environ.setdefault('PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION', 'python')
from gz.transport13 import Node
from gz.msgs10.image_pb2 import Image
" 2>/dev/null; then
  ok "gz python bindings" "gz-transport13 + gz-msgs10"
else
  warn "gz python bindings" "missing - dashboard runs, camera feeds do not"
  fix "sudo apt install python3-gz-transport13 python3-gz-msgs10"
fi
echo

# --- this repo --------------------------------------------------------------
echo "This repo"
if command -v flyit > /dev/null 2>&1; then
  ok "flyit on PATH" "$(command -v flyit)"
else
  warn "flyit on PATH" "not linked - use ./flyit from the repo"
  fix "ln -sfn \"$REPO/flyit\" ~/.local/bin/flyit"
fi

LEFT=$(running_vehicles)
if [ "$LEFT" = "0" ]; then
  ok "no stale SITL" "nothing running"
else
  warn "stale SITL" "$LEFT process(es) still up"
  fix "./flyit --stop"
fi

# A busy 8760 makes flyit look like the dashboard failed to start, when in
# fact something else already answers there.
if python3 -c "
import socket, sys
s = socket.socket()
try:
    s.bind(('127.0.0.1', $DASHBOARD_HTTP_PORT)); sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()" 2>/dev/null; then
  ok "port $DASHBOARD_HTTP_PORT" "free for the dashboard"
else
  warn "port $DASHBOARD_HTTP_PORT" "in use"
  fix "./flyit --stop, or DASHBOARD_HTTP_PORT=8761 flyit"
fi
echo

echo "=============================================="
echo "  $PASS ok, $WARN warn, $FAIL fail"
if [ "$FAIL" = "0" ]; then
  echo "RESULT: PASS - ready to run: flyit"
  [ "$WARN" != "0" ] && echo "         (warnings above cost features, not the run)"
  echo "=============================================="
  exit 0
fi
NOUN="dependencies"; [ "$FAIL" = "1" ] && NOUN="dependency"
echo "RESULT: FAIL - $FAIL required $NOUN missing. Run ./install.sh"
echo "=============================================="
exit 1
