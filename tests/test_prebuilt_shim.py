"""Without clang and lld (the packaged emulator), build_shim uses the copy compiled ahead, and refuses a stale one."""
import hashlib, os, pathlib, sys, tempfile
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import q2emu
ok = lambda c, what: print(('PASS ' if c else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp()); root = tmp/'rootfs'; (root/'lib').mkdir(parents=True)
stamp = q2emu.PREBUILT.with_name(q2emu.PREBUILT.name + '.sha256')
digest = hashlib.sha256((q2emu.HERE/'shim.c').read_bytes()).hexdigest()
ok(q2emu.PREBUILT.exists() and stamp.read_text().strip() == digest,
   'emulator/libq2emu.so is compiled from the current shim.c (else run tools/build_shim.py)')
ok(q2emu.PREBUILT.read_bytes()[:20] == b'\x7fELF\x01\x01\x01' + bytes(9) + b'\x03\x00\x08\x00', 'it is a MIPS little-endian shared library')
os.environ['PATH'] = str(tmp)  # no clang, no lld
q2emu.build_shim(root, tmp)
ok((root/'lib/libq2emu.so').read_bytes() == q2emu.PREBUILT.read_bytes(), 'without a compiler, the ready-made copy goes into the rootfs')
real = q2emu.PREBUILT
try:
    q2emu.PREBUILT = tmp/'libq2emu.so'; q2emu.PREBUILT.write_bytes(real.read_bytes())
    (tmp/'libq2emu.so.sha256').write_text('0' * 64 + '\n')
    try: q2emu.build_shim(root, tmp); ok(False, 'a copy from another shim.c is refused')
    except RuntimeError: ok(True, 'a copy from another shim.c is refused')
finally:
    q2emu.PREBUILT = real
ok(q2emu.symbol(q2emu.PREBUILT, 'readdir'), 'it answers readdir(): the SD card folder lists on a PC filesystem')