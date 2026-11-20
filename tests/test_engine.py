"""Non-root checks of the cleanup. Uses copies only; never touches ~/.cache/q2emu or the run folders."""
import hashlib, os, pathlib, shutil, sys, tempfile, threading, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import q2emu, emulator_gui as g

H = pathlib.Path.home()
FIX = __import__('pathlib').Path(__file__).resolve().parent / 'fixtures'
STOCK = FIX/'demo-stock'        # unpatched stock V1.36
STOCK_STUB = FIX/'demo-stock-stubbed'               # the hand-made, verified stock stub
POD_STUB = FIX/'demo-pod-stubbed'             # the hand-made, verified Pod stub
sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:16]
ok = lambda cond, what: print(('PASS ' if cond else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp())

# 1. symbol lookup
for p in (STOCK, POD_STUB):
    ok(q2emu.symbol(p, 'home_page_init') == 0x534b6c, f'home_page_init found in {p.name}')
    ok(q2emu.symbol(p, 'g_backlight_status') == 0x9b3214, f'g_backlight_status found in {p.name}')
ok(q2emu.find_qemu() == str(FIX.parents[1]/'bin/qemu-mipsel-static') or 'Q2EMU_QEMU' in os.environ, f'QEMU found in the project: {q2emu.find_qemu()}')
ok(q2emu.symbol(STOCK, 'no_such_symbol_here') is None, 'missing symbol gives None')

# 2. serial patch by symbol == the verified hand-made stubs
d = tmp/'demo'; shutil.copy(STOCK, d); q2emu._patch_serial_check(d)
ok(sha(d) == sha(STOCK_STUB), f'stock patched == demo-stubbed ({sha(d)} vs {sha(STOCK_STUB)})')
before = sha(d); q2emu._patch_serial_check(d); ok(sha(d) == before, 'patching twice changes nothing')
shutil.copy(POD_STUB, d); q2emu._patch_serial_check(d); ok(sha(d) == sha(POD_STUB), 'already-patched Pod demo left alone')
bad = tmp/'bad'; data = bytearray(STOCK.read_bytes()); off = q2emu._file_offset(data, 0x534bac)
data[off:off+4] = b'\0\0\0\0'; bad.write_bytes(bytes(data))
try: q2emu._patch_serial_check(bad); ok(False, 'unknown build refused')
except RuntimeError as e: ok('differs' in str(e), 'unknown build refused with a clear error')

# 3. prepare(): battery, SD slot, backlight address in the start script (binfmt + mknod faked)
root = tmp/'rootfs'; (root/'release/bin').mkdir(parents=True); (root/'lib').mkdir()
shutil.copy(POD_STUB, root/'release/bin/demo')
misc = tmp/'binfmt'; misc.mkdir(); (misc/'register').write_text('')
q2emu.BINFMT_RULE = misc/'q2mipsel'
calls = []; q2emu.run = lambda *a, **k: calls.append([str(x) for x in a])
os.environ['Q2EMU_QEMU'] = '/fake/qemu'
sd = tmp/'music'; sd.mkdir()
q2emu.prepare(root, sd)
start = (root/'q2emu-start.sh').read_text()
ok('Q2EMU_BL_ADDR=0x9b3214 LD_PRELOAD=' in start, 'start script passes the backlight address')
ok(all((root/p).read_text().strip() == v for p, v in q2emu.BATTERY.items()), 'battery files written')
ok(all((root/p).exists() for p in q2emu.SD_SLOT + ['tmp/mmc_add']), 'SD slot nodes + mmc_add made with a card')
ok(['mount', '--bind', str(sd), str(root/'mnt/mmc')] in calls, 'SD folder bind-mounted')
q2emu.prepare(root, None)
ok(not any((root/p).exists() for p in q2emu.SD_SLOT + ['tmp/mmc_add']), 'no card: slot nodes + mmc_add removed')

# 4. lock: a second holder is refused
w = tmp/'work'; l1 = q2emu.lock(w)
try: q2emu.lock(w); ok(False, 'second lock refused')
except RuntimeError: ok(True, 'second lock refused')
l1.close(); l2 = q2emu.lock(w); ok(True, 'lock free again after close'); l2.close()

# 5. runner: same file keeps settings, new file starts clean, errors surface, load-while-loading refused
unpacks = []
def fake_unpack(fw, r):
    time.sleep(0.3); unpacks.append(fw.name); (r/'release/bin').mkdir(parents=True); (r/'mnt/data').mkdir(parents=True)
class FakeProc:
    def __init__(self): self.returncode = None
    def poll(self): return self.returncode
class FakeQ2:
    started = lambda s: True; screen_off = lambda s: getattr(s, 'off', False)
    def __init__(self, r, l): self.proc = FakeProc(); self.got = []
    def stop(self): self.proc.returncode = -15
    def key(self, n): self.got.append(('key', n))
    def touch(self, *a, **k): self.got.append(('touch', a, k.get('first', False)))
    def wheel(self, t): time.sleep(0.05); self.got.append(('wheel', t))
g.unpack = fake_unpack; g._patch_serial_check = lambda p: None; g.build_shim = lambda r, w: None
g.prepare = lambda *a, **k: None; g.Q2 = FakeQ2
fa = tmp/'a.tar'; fa.write_bytes(b'A'); fb = tmp/'b.tar'; fb.write_bytes(b'B fork')
r = g.FirmwareRunner(tmp/'w2')
def wait():
    while r.loading: time.sleep(0.02)
ok(r.load(fa) and not r.load(fb), 'second load while loading is refused'); wait()
ok(r.state == 'running', 'state running after load')
(r.root/'mnt/data/config.ini').write_text('LANGUAGE=1')
r.load(fa); wait(); ok((r.root/'mnt/data/config.ini').exists(), 'same firmware keeps its settings')
# input order through the queue
r.send(lambda q: q.wheel(1)); r.send(lambda q: q.key('centre')); r.send(lambda q: q.touch(5, 6, first=True))
time.sleep(0.4); ok([x[0] for x in r.q2.got] == ['wheel', 'key', 'touch'], f'inputs arrive in order: {r.q2.got}')
r.q2.proc.returncode = 1; r.poll(); ok(r.state == 'stopped' and 'exit status 1' in r.message, 'firmware exit noticed')
r.load(fb); wait(); ok(not (r.root/'mnt/data/config.ini').exists(), 'different firmware starts clean')
ok(unpacks == ['a.tar', 'b.tar'], f'unpacked only when the file changed: {unpacks}')
g.prepare = lambda *a, **k: sys.exit('qemu-mipsel-static is missing')
r.load(fa); wait(); ok(r.state == 'error' and 'QEMU program' in r.message, 'sys.exit in loader becomes an error state')
g.prepare = lambda *a, **k: None
# closing during a load: the loader must stop what it starts
r.load(fb); time.sleep(0.05); r.close(); wait()
ok(r.q2 is None, 'firmware started after close is stopped by the loader')
print('done')
