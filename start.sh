#!/bin/bash
# Start the Q2 Emulator. Pass a firmware file to open it straight away:  ./start.sh path/to/update.tar
# Needs root (chroot, mounts, binfmt_misc), so it asks for the sudo password.
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/opt/q2-venv/bin/python3; [ -x $PY ] || PY=python3
if pgrep -f 'emulator/emulator_gui.py' >/dev/null; then
  echo 'An emulator window is still open. Close it with its X button; this will continue by itself.'
  while pgrep -f 'emulator/emulator_gui.py' >/dev/null; do sleep 1; done
fi
echo 'Type your Ubuntu password below (it stays invisible as you type), then press Enter:'
exec sudo -E DISPLAY="${DISPLAY:-:0}" $PY "$HERE/emulator/emulator_gui.py" ${1:+--firmware "$1"}
