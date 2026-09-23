#!/usr/bin/env bash
# install.sh - everything a fresh clone needs, in one command.
#
#   ./install.sh                     the lot: apt, Gazebo, ArduPilot, Python
#   ./install.sh --deps-dir ~/x      put ArduPilot and the plugin somewhere else
#                                    (or export SSS_ROOT=~/x, same thing)
#   ./install.sh --no-gazebo         skip Gazebo (headless physics only)
#   ./install.sh --no-apt            no sudo, no apt - you handle system packages
#   ./install.sh --yes               don't ask before the long steps
#   ./install.sh --check             check what is installed, change nothing
#
# Roughly 20-40 minutes on a fresh machine, nearly all of it compiling
# ArduPilot. Safe to re-run: every step checks for its own result first, so a
# second run after a failure picks up where the first stopped rather than
# starting over.
#
# What it installs, and why each one is here:
#   apt packages        compilers and git, plus the Gazebo Python bindings,
#                       which pip cannot supply
#   Gazebo Harmonic     the 3D physics and the camera sensors
#   ArduPilot + SITL    the actual autopilot, compiled for this machine
#   ardupilot_gazebo    the plugin joining the two - flight dynamics over a
#                       socket, one FDM port per vehicle
#   Python packages     pymavlink, MAVProxy, OpenCV (see requirements.txt)
#   config/local.env    where it all ended up, so the scripts find it
#   ~/.local/bin/flyit  so `flyit` works from any directory
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEPS_DIR=""
WANT_GAZEBO=1
WANT_APT=1
ASSUME_YES=0
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --deps-dir) DEPS_DIR="${2:?--deps-dir needs a path}"; shift 2 ;;
    --deps-dir=*) DEPS_DIR="${1#*=}"; shift ;;
    --no-gazebo) WANT_GAZEBO=0; shift ;;
    --no-apt)    WANT_APT=0; shift ;;
    -y|--yes)    ASSUME_YES=1; shift ;;
    --check)     CHECK_ONLY=1; shift ;;
    -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done

if [ "$CHECK_ONLY" = "1" ]; then
  exec "$REPO/scripts/check_setup.sh"
fi

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {
  [ "$ASSUME_YES" = "1" ] && return 0
  local reply
  read -r -p "    $1 [Y/n] " reply < /dev/tty || return 0
  case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# --- where the dependencies go ----------------------------------------------
# Default beside the repo rather than inside it: they are large, they are other
# people's repositories, and one shared copy can serve several checkouts of
# this one. scripts/env.sh looks here on its own, so this needs no config file
# to work - local.env below just makes the choice explicit and survivable.
if [ -z "$DEPS_DIR" ]; then
  if [ -n "${SSS_ROOT:-}" ]; then
    DEPS_DIR="$SSS_ROOT"                                 # same variable the scripts read
  elif [ -d "$REPO/../SSS_2026/ardupilot" ]; then
    DEPS_DIR="$(cd "$REPO/../SSS_2026" && pwd)"          # existing rig, reuse it
  else
    DEPS_DIR="$HOME/swarm-deps"
  fi
fi
mkdir -p "$DEPS_DIR" || die "cannot create $DEPS_DIR"
DEPS_DIR="$(cd "$DEPS_DIR" && pwd)"

ARDUPILOT="$DEPS_DIR/ardupilot"
ARDUPILOT_GAZEBO="$DEPS_DIR/ardupilot_gazebo"

echo "=============================================="
echo " Swarm-Simulation-SSS - install"
echo "=============================================="
note "repo         $REPO"
note "dependencies $DEPS_DIR"
note "gazebo       $([ "$WANT_GAZEBO" = 1 ] && echo yes || echo 'no (--no-gazebo)')"

# --- the host ---------------------------------------------------------------
UBUNTU_CODENAME=""
if [ -r /etc/os-release ]; then . /etc/os-release; fi
case "${ID:-}:${VERSION_ID:-}" in
  ubuntu:22.04|ubuntu:24.04) note "host         ${PRETTY_NAME:-}" ;;
  *)
    note "host         ${PRETTY_NAME:-unknown}"
    echo
    note "This is written for Ubuntu 22.04 and 24.04. Elsewhere the apt steps"
    note "will not fit; run with --no-apt and install the equivalents yourself"
    note "(a compiler toolchain, Gazebo Harmonic, its Python bindings)."
    ask "Continue anyway?" || exit 1
    [ "${ID:-}" != "ubuntu" ] && WANT_APT=0
    ;;
esac

if [ "$WANT_APT" = "1" ] && ! command -v sudo > /dev/null 2>&1; then
  note "no sudo on this machine; skipping the apt steps."
  WANT_APT=0
fi

# --- 1. system packages -----------------------------------------------------
say "1/6  System packages"
if [ "$WANT_APT" = "0" ]; then
  note "skipped (--no-apt). You need: build-essential cmake git python3-pip"
  note "plus, for Gazebo mode, Gazebo Harmonic and python3-gz-transport13."
else
  note "sudo apt-get install build-essential cmake git python3-pip ..."
  sudo apt-get update -qq || die "apt-get update failed"
  sudo apt-get install -y \
    build-essential cmake git curl wget lsb-release gnupg \
    python3 python3-pip python3-dev python3-venv \
    libgz-sim8-dev rapidjson-dev 2>/dev/null \
  || sudo apt-get install -y \
       build-essential cmake git curl wget lsb-release gnupg \
       python3 python3-pip python3-dev python3-venv rapidjson-dev \
  || die "apt-get install failed"
  note "done."
fi

# --- 2. Gazebo --------------------------------------------------------------
say "2/6  Gazebo Harmonic"
if [ "$WANT_GAZEBO" = "0" ]; then
  note "skipped (--no-gazebo). flyit --no-gazebo runs headless physics; you"
  note "lose the 3D view, the camera feeds and the accident detection."
elif command -v gz > /dev/null 2>&1; then
  note "already installed: gz sim $(gz sim --versions 2>/dev/null | head -1)"
elif [ "$WANT_APT" = "0" ]; then
  note "not installed, and --no-apt was given. See:"
  note "  https://gazebosim.org/docs/harmonic/install_ubuntu"
else
  note "adding the OSRF apt repository..."
  sudo curl -fsSL https://packages.osrfoundation.org/gazebo.gpg \
       -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    || die "could not fetch the OSRF signing key"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
  sudo apt-get update -qq || die "apt-get update failed after adding the repo"
  # The Python bindings are a separate package from Gazebo itself, and they are
  # what the dashboard's camera feeds subscribe with. pip has no equivalent.
  sudo apt-get install -y gz-harmonic libgz-sim8-dev \
       python3-gz-transport13 python3-gz-msgs10 \
    || die "installing Gazebo failed"
  note "installed: gz sim $(gz sim --versions 2>/dev/null | head -1)"
fi

# --- 3. Python packages -----------------------------------------------------
say "3/6  Python packages"
note "pip3 install --user -r requirements.txt"
python3 -m pip install --user --upgrade pip -q 2>/dev/null
if python3 -m pip install --user -q -r "$REPO/requirements.txt"; then
  note "done."
else
  # Debian/Ubuntu 24.04 marks the system Python externally managed (PEP 668).
  note "pip refused a --user install (PEP 668). Retrying with --break-system-packages,"
  note "which here means 'install into ~/.local anyway' - it does not touch /usr."
  python3 -m pip install --user -q --break-system-packages -r "$REPO/requirements.txt" \
    || die "pip install failed. See requirements.txt for what is needed."
fi

# pip puts mavproxy.py in ~/.local/bin, which is not on PATH on a fresh account.
# sim_vehicle.py execs it by name, so a missing PATH entry fails every launch.
if ! command -v mavproxy.py > /dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/mavproxy.py" ]; then
    note "~/.local/bin is not on your PATH, but MAVProxy is installed there."
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
      [ -f "$rc" ] || continue
      grep -q '\.local/bin' "$rc" && continue
      echo '' >> "$rc"
      echo '# added by Swarm-Simulation-SSS/install.sh' >> "$rc"
      echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
      note "added it to $(basename "$rc")"
    done
    export PATH="$HOME/.local/bin:$PATH"
    note "open a new terminal afterwards, or: export PATH=\"\$HOME/.local/bin:\$PATH\""
  else
    die "MAVProxy did not install. sim_vehicle.py cannot start a vehicle without it."
  fi
fi
note "MAVProxy at $(command -v mavproxy.py)"

# --- 4. ArduPilot -----------------------------------------------------------
say "4/6  ArduPilot SITL"
if [ ! -d "$ARDUPILOT/.git" ]; then
  note "cloning ArduPilot into $ARDUPILOT (a few minutes, ~1 GB)..."
  git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git "$ARDUPILOT" \
    || die "git clone failed"
else
  note "checkout present: $ARDUPILOT"
  git -C "$ARDUPILOT" submodule update --init --recursive -q || true
fi

if [ "$WANT_APT" = "1" ] && [ -x "$ARDUPILOT/Tools/environment_install/install-prereqs-ubuntu.sh" ]; then
  # ArduPilot's own prerequisite script. -y so it does not stop to ask, and
  # it is idempotent, so re-running install.sh just re-checks.
  note "running ArduPilot's install-prereqs-ubuntu.sh..."
  ( cd "$ARDUPILOT" && ./Tools/environment_install/install-prereqs-ubuntu.sh -y ) \
    || note "prereqs script reported problems; continuing - the build will say if it matters."
fi

if [ -x "$ARDUPILOT/build/sitl/bin/arducopter" ]; then
  note "SITL binary already built."
else
  note "building ArduCopter for SITL. This is the long step (10-30 min)."
  ask "Build now?" || die "stopped before the build. Re-run ./install.sh to resume."
  ( cd "$ARDUPILOT" && ./waf configure --board sitl && ./waf copter ) \
    || die "the ArduPilot build failed. Its output above says why."
fi
note "arducopter: $ARDUPILOT/build/sitl/bin/arducopter"

# --- 5. ardupilot_gazebo ----------------------------------------------------
say "5/6  ardupilot_gazebo plugin"
if [ "$WANT_GAZEBO" = "0" ]; then
  note "skipped with Gazebo."
elif [ -f "$ARDUPILOT_GAZEBO/build/libArduPilotPlugin.so" ]; then
  note "already built: $ARDUPILOT_GAZEBO/build"
elif ! command -v gz > /dev/null 2>&1; then
  note "no Gazebo, so nothing to build against. Skipped."
else
  [ -d "$ARDUPILOT_GAZEBO/.git" ] || \
    git clone https://github.com/ArduPilot/ardupilot_gazebo.git "$ARDUPILOT_GAZEBO" \
      || die "could not clone ardupilot_gazebo"
  note "building the plugin..."
  mkdir -p "$ARDUPILOT_GAZEBO/build"
  ( cd "$ARDUPILOT_GAZEBO/build" \
      && cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      && make -j"$(nproc)" ) \
    || die "the plugin build failed. Usually a missing libgz-sim8-dev."
  note "built: $ARDUPILOT_GAZEBO/build/libArduPilotPlugin.so"
fi

# --- 6. wire this repo up ---------------------------------------------------
say "6/6  This repo"

# Record the paths rather than leaving them to autodetection. Autodetection is
# what makes a plain clone work; this file is what makes it keep working after
# someone moves a directory.
# Only SSS_ROOT is written as a literal path; the rest hang off it, so
# `export SSS_ROOT=/somewhere/else` moves all four together. Pinning each one
# absolutely would let SSS_ROOT move while ARDUPILOT silently stayed behind.
cat > "$REPO/config/local.env" << EOF
# Written by ./install.sh on $(date -Iseconds). Gitignored: your paths, your
# machine. Edit freely, or delete it to fall back to autodetection.
#
# Every line is "default unless already set", so an exported variable wins.
: "\${SSS_ROOT:=$DEPS_DIR}"
: "\${ARDUPILOT:=\$SSS_ROOT/ardupilot}"
: "\${ARDUPILOT_GAZEBO:=\$SSS_ROOT/ardupilot_gazebo}"
: "\${MISSION_PLANNER:=\$SSS_ROOT/MissionPlanner}"
EOF
note "wrote config/local.env"

chmod +x "$REPO/flyit" "$REPO"/scripts/*.sh 2>/dev/null

mkdir -p "$HOME/.local/bin"
if [ -e "$HOME/.local/bin/flyit" ] && [ "$(readlink -f "$HOME/.local/bin/flyit")" != "$REPO/flyit" ]; then
  note "~/.local/bin/flyit points somewhere else; leaving it alone. Use ./flyit."
else
  ln -sfn "$REPO/flyit" "$HOME/.local/bin/flyit"
  note "linked ~/.local/bin/flyit -> $REPO/flyit"
fi

# --- and check it -----------------------------------------------------------
echo
"$REPO/scripts/check_setup.sh"
STATUS=$?

echo
if [ "$STATUS" = "0" ]; then
  cat << EOF
==============================================
 Installed.

   flyit              3 drones in Gazebo + dashboard at http://127.0.0.1:8760
   flyit 2            two drones
   flyit --no-gazebo  headless physics, much faster to start
   flyit --stop       shut it all down

 If 'flyit' is not found, open a new terminal (the PATH entry is new) or
 run ./flyit from this directory.

 First launch takes longer: ArduPilot writes each vehicle's parameter store,
 and GPS/EKF need about a minute to settle before the arm button enables.
==============================================
EOF
else
  cat << EOF
==============================================
 Install finished, but the check above found missing pieces.
 Fix what it lists, then re-run:  ./install.sh
==============================================
EOF
fi
exit $STATUS
