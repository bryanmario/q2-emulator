#!/bin/sh
# Turn a fresh Alpine mini root filesystem into the emulator's private Linux.
# Run as root inside the WSL distro being built (see build.ps1).
set -e
apk update -q
# python + Tk + Pillow: the window.  squashfs-tools: unpacking firmware.  util-linux: unshare, setpriv, mount.
# ffmpeg: recording.  font-dejavu + fontconfig: the window's text.  slirp4netns: the player's internet.
apk add -q python3 py3-pillow python3-tkinter tk squashfs-tools util-linux ffmpeg font-dejavu fontconfig slirp4netns
fc-cache -f >/dev/null
cat > /etc/wsl.conf <<'EOF'
[user]
default=root
[interop]
appendWindowsPath=false
EOF
rm -rf /var/cache/apk/*
echo "private Linux ready: $(cat /etc/alpine-release), $(du -sh / -x 2>/dev/null | cut -f1)"
