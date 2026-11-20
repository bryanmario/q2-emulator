#!/usr/bin/env python3
"""Compile emulator/shim.c ahead of time, for machines without clang and lld (the packaged emulator).

  tools/build_shim.py path/to/libc.so.6      # a firmware's own libc, e.g. ~/.cache/q2emu/rootfs/lib/libc.so.6

Writes emulator/libq2emu.so and, beside it, the hash of the shim.c it was compiled from: q2emu only uses
the copy while that hash still matches. Run it again whenever shim.c changes."""
import hashlib, pathlib, sys, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'emulator'))
import q2emu

if len(sys.argv) != 2:
    sys.exit(__doc__)
with tempfile.TemporaryDirectory() as tmp:
    q2emu.compile_shim(pathlib.Path(sys.argv[1]), q2emu.PREBUILT, pathlib.Path(tmp))
digest = hashlib.sha256((ROOT/'emulator/shim.c').read_bytes()).hexdigest()
q2emu.PREBUILT.with_name(q2emu.PREBUILT.name + '.sha256').write_text(digest + '\n')
print(f'{q2emu.PREBUILT} ({q2emu.PREBUILT.stat().st_size} bytes) from shim.c {digest[:16]}')
