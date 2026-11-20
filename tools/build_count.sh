#!/bin/bash
# Build bin/count/: the QEMU of the emulator's measuring mode, and its counter (emulator/q2count.c).
#
# It is the same QEMU as bin/qemu-mipsel-static (11.1.3, source unchanged) built with its plugin
# interface, which a static build cannot have. So it needs its libraries, and the box it runs in is the
# firmware's root, where there are none of the PC's: they are put beside it, and q2emu copies the folder
# into the box as /q2emu-host, where this QEMU is told to find them (its loader and its library path
# are set to that folder here).
#
#   tools/build_count.sh [folder to build QEMU in, default ~/q2-ref/qemu]
#
# Needs: build-essential ninja-build pkg-config libglib2.0-dev python3-venv flex bison patchelf wget.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${1:-$HOME/q2-ref/qemu}; V=11.1.3; OUT=$HERE/bin/count; BOX=/q2emu-host
mkdir -p "$WORK" && cd "$WORK"
[ -f qemu-$V.tar.xz ] || wget -q https://download.qemu.org/qemu-$V.tar.xz
[ -d qemu-$V ] || tar xf qemu-$V.tar.xz
mkdir -p qemu-$V/build && cd qemu-$V/build
[ -f build.ninja ] || ../configure --target-list=mipsel-linux-user --enable-plugins --disable-docs --disable-werror --disable-capstone > configure.log 2>&1
ninja qemu-mipsel > ninja.log 2>&1
rm -rf "$OUT" && mkdir -p "$OUT"
gcc -O2 -shared -fPIC -Wall $(pkg-config --cflags glib-2.0) -I../include/plugins "$HERE/emulator/q2count.c" -o "$OUT/libq2count.so"
cp qemu-mipsel "$OUT/qemu-mipsel"
for lib in $(ldd qemu-mipsel | awk '$3 ~ /^\// {print $3}'); do cp -L "$lib" "$OUT/"; done
cp -L "$(ldd qemu-mipsel | awk '/ld-linux/ {print $1}')" "$OUT/"
# (the C library loads this one by name when a thread ends; without it QEMU aborts at the firmware's first thread exit)
cp -L "$(gcc -print-file-name=libgcc_s.so.1)" "$OUT/"
LD=$(basename "$(ldd qemu-mipsel | awk '/ld-linux/ {print $1}')")
# (the old-style path, which also holds for the libraries' own libraries: the firmware's /usr/lib has MIPS ones of the same names)
patchelf --set-interpreter $BOX/$LD --force-rpath --set-rpath $BOX "$OUT/qemu-mipsel"
gcc -O2 -static -Wall "$HERE/emulator/q2count_run.c" -o "$OUT/q2count-run"
strip "$OUT/qemu-mipsel"
# the licenses of what is bundled here go with it (docs/THIRD-PARTY.md)
mkdir -p "$OUT/licenses"
for pkg in libc6 libgcc-s1 libglib2.0-0t64 libpcre2-8-0 zlib1g; do
    [ -r /usr/share/doc/$pkg/copyright ] && cp /usr/share/doc/$pkg/copyright "$OUT/licenses/$pkg.txt"
done
cp ../COPYING "$OUT/licenses/qemu-COPYING.txt"; cp ../COPYING.LIB "$OUT/licenses/qemu-COPYING.LIB.txt" 2>/dev/null
ls -la "$OUT"; du -sh "$OUT"
