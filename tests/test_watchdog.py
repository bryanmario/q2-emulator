import os, pathlib, shutil, sys, tempfile
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
FIX = __import__('pathlib').Path(__file__).resolve().parent / 'fixtures'
import q2emu
t = pathlib.Path(tempfile.mkdtemp()); root = t/'rootfs'
(root/'release/bin').mkdir(parents=True); (root/'usr/bin').mkdir(parents=True)
shutil.copy(FIX/'demo-pod-stubbed', root/'release/bin/demo')
real = pathlib.Path.home()/'.cache/q2emu/rootfs/usr/bin/checkappprocess.sh.q2emu-orig'
src = real if real.exists() else FIX/'checkappprocess.sh'
shutil.copy(src, root/q2emu.WATCHDOG); original = (root/q2emu.WATCHDOG).read_text()
misc = t/'binfmt'; misc.mkdir(); (misc/'register').write_text('')
q2emu.BINFMT_RULE = misc/'q2mipsel'; q2emu.run = lambda *a, **k: None; os.environ['Q2EMU_QEMU'] = '/fake'
for i in (1, 2):  # twice: a second launch must keep the true original
    q2emu.prepare(root, None)
w = root/q2emu.WATCHDOG; orig = w.with_name(w.name + '.q2emu-orig')
print('PASS' if w.read_text() == q2emu.WATCHDOG_STANDIN and os.access(w, os.X_OK) else 'FAIL', 'watchdog replaced by stand-in, executable')
print('PASS' if orig.read_text() == original and 'reboot' in original else 'FAIL', 'original kept beside it, unchanged')
