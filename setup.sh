#!/usr/bin/env bash
# One-time setup for Crazyflie 2.1 Brushless on macOS.
set -e

echo "==> Checking Homebrew libusb (needed by the Crazyradio driver on macOS)"
if ! brew list libusb >/dev/null 2>&1; then
  brew install libusb
else
  echo "    libusb already installed"
fi

echo "==> Creating virtual environment (.venv)"
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing cflib + cfclient"
pip install --upgrade pip
pip install cflib cfclient

echo
echo "Done. Every new terminal, run:  source .venv/bin/activate"
echo "Then:  python 1_scan.py"
