#!/usr/bin/env bash
# Tear down everything this repo starts, and confirm it is gone.
source "$(dirname "$0")/env.sh"
echo "Stopping simulation..."
if kill_sim; then
  echo "All simulation processes stopped."
else
  exit 1
fi
