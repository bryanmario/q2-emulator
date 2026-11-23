#!/bin/sh
# Q2 Emulator's install step. Q2Emulator.exe (launcher.cs) runs it as root in the emulator's private Linux
# the first time and after each update, with the app folder (data\app, as Linux names it) as its argument:
#   sh <app>/install.sh <app>
# It copies the app to /opt/q2-emulator and writes the drive lock (/etc/wsl.conf: the PC's drives read-only
# in the box, see emulator/drives.py), so installs made before the lock get it too.
# Exit 0: done. Exit 3: done, but the drives are still mounted writable: WSL must restart this Linux
# (wsl --terminate Q2Emulator) before the emulator starts, for the lock to take hold.
set -eu
src=${1:-}
[ -n "$src" ] && [ -f "$src/emulator/q2emu.py" ] || { echo "install.sh: not the emulator's app folder: '$src'" >&2; exit 1; }

# The old copy goes, then the new one comes. The path is written out, never built, and checked first:
# a plain folder, with nothing mounted inside it.
if [ -e /opt/q2-emulator ] || [ -L /opt/q2-emulator ]; then
    if [ -L /opt/q2-emulator ] || [ ! -d /opt/q2-emulator ]; then echo "install.sh: /opt/q2-emulator is not a plain folder" >&2; exit 1; fi
    if grep -q ' /opt/q2-emulator[/ ]' /proc/self/mountinfo; then echo "install.sh: something is mounted in /opt/q2-emulator" >&2; exit 1; fi
    rm -rf -- /opt/q2-emulator
fi
mkdir -p /opt
cp -r "$src" /opt/q2-emulator
chmod +x /opt/q2-emulator/bin/qemu-mipsel-static
# the taskbar icon: WSL's display takes it from a desktop entry (emulator/q2emulator.desktop)
mkdir -p /usr/share/applications /usr/share/pixmaps
cp /opt/q2-emulator/emulator/icon.png /usr/share/pixmaps/q2emulator.png
cp /opt/q2-emulator/emulator/q2emulator.desktop /usr/share/applications/q2emulator.desktop

# The drive lock. The same text as packaging/setup-rootfs.sh writes (tests/test_drive_lock.py checks).
cat > /etc/wsl.conf <<'EOF'
[user]
default=root
[interop]
appendWindowsPath=false
[automount]
options = "ro"
EOF

# Mounted before the lock was written (an install from before 1.3): a drive still writable.
if awk '$5 ~ /^\/mnt\/[a-z]$/ && $6 ~ /(^|,)rw(,|$)/ { found = 1 } END { exit !found }' /proc/self/mountinfo; then exit 3; fi
exit 0
