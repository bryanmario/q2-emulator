# What the download bundles

Q2 Emulator itself is under the GPL v3 (`LICENSE`). `Q2Emulator.exe` also carries these, each under its own
license. No firmware is included.

| What | Why | License | Source |
|---|---|---|---|
| QEMU 11.1.3, `qemu-mipsel` (two builds: static, and with plugins for measuring mode) | runs the firmware's MIPS programs | GPL v2 | https://download.qemu.org/qemu-11.1.3.tar.xz, unchanged; built as `docs/DEVELOPMENT.md` and `tools/build_count.sh` say |
| GNU C Library, `libgcc` | the measuring-mode QEMU's libraries | LGPL v2.1 or later; GPL v3 with the GCC Runtime Library Exception | Ubuntu 24.04's packages `libc6`, `libgcc-s1` |
| GLib | the same | LGPL v2.1 or later | Ubuntu 24.04's `libglib2.0-0t64` |
| PCRE2 | the same | BSD 3-clause | Ubuntu 24.04's `libpcre2-8-0` |
| zlib | the same | zlib license | Ubuntu 24.04's `zlib1g` |
| Alpine Linux 3.24 with Python, Tk, Pillow, FFmpeg, squashfs-tools, util-linux, slirp4netns, DejaVu fonts | the private Linux the emulator runs in | each package's own (GPL, LGPL, PSF, BSD, MIT and others) | https://alpinelinux.org, packages as `packaging/setup-rootfs.sh` installs them |

The full license texts of the measuring-mode libraries are in the download, in `data\app\bin\count\licenses`.
Source for any GPL or LGPL part is at the links above; ask in the project's issues if you cannot get it there.
