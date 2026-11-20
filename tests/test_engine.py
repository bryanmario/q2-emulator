"""Non-root checks of the cleanup. Uses copies only; never touches ~/.cache/q2emu or the run folders."""
import hashlib, os, pathlib, shutil, sys, tempfile, threading, time, types
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
    def key(self, n, ms=80): self.got.append(('key', n))
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
# the firmware's programs that work missing hardware are swapped for stand-ins, and swapped back
root = tmp/'standins'; (root/'usr/bin').mkdir(parents=True); (root/'sbin').mkdir()
(root/'usr/bin/rtk_hciattach').write_bytes(b'\x7fELF real'); (root/'bin').mkdir(); (root/'bin/busybox').write_bytes(b'bb')
os.symlink('../bin/busybox', root/'sbin/udhcpc')
q2emu.stand_in(root, 'usr/bin/rtk_hciattach', 'exit 0\n'); q2emu.stand_in(root, 'sbin/udhcpc', 'exit 0\n')
q2emu.stand_in(root, 'sbin/udhcpc', 'exit 0\n')   # twice: the kept original must stay the original
ok((root/'usr/bin/rtk_hciattach').read_text().startswith('#!/bin/sh\n# q2emu stand-in') and os.access(root/'usr/bin/rtk_hciattach', os.X_OK)
   and (root/'usr/bin/rtk_hciattach.q2emu-orig').read_bytes() == b'\x7fELF real', 'a stand-in replaces the program and keeps the original beside it')
ok(not (root/'sbin/udhcpc').is_symlink() and os.readlink(root/'sbin/udhcpc.q2emu-orig') == '../bin/busybox', 'a busybox link is kept as the link')
q2emu.original(root, 'sbin/udhcpc'); q2emu.original(root, 'usr/bin/rtk_hciattach'); q2emu.original(root, 'usr/bin/rtk_hciattach')
ok(os.readlink(root/'sbin/udhcpc') == '../bin/busybox' and (root/'usr/bin/rtk_hciattach').read_bytes() == b'\x7fELF real'
   and not (root/'sbin/udhcpc.q2emu-orig').is_symlink(), 'and the originals come back when the stand-ins go')

# the pretend Wi-Fi: wpa_supplicant's answers, as the firmware asks for them
import wifi, socket
wroot = tmp/'wifiroot'; (wroot/'run').mkdir(parents=True); (wroot/'var').mkdir(); os.symlink('../run', wroot/'var/run')
(wroot/'mnt/data').mkdir(parents=True); (wroot/'tmp').mkdir()
w = wifi.Wifi(wroot)
ok(w.answer('PING') == 'PONG\n' and 'wpa_state=INACTIVE' in w.answer('STATUS') and wifi.SSID in w.answer('SCAN_RESULTS'),
   'Wi-Fi: not connected at first, and one network is in the air')
i = w.answer('ADD_NETWORK').strip()
for c in (f'SET_NETWORK {i} ssid "{wifi.SSID}"', f'SET_NETWORK {i} key_mgmt NONE', f'ENABLE_NETWORK {i}', 'SAVE_CONFIG', f'SELECT_NETWORK {i}'):
    ok(w.answer(c) == 'OK\n', f'Wi-Fi: {c.split()[0]} accepted')
s = w.answer('STATUS')
ok('wpa_state=COMPLETED' in s and f'ssid={wifi.SSID}' in s and f'ip_address={wifi.ADDRESS}' in s, 'Wi-Fi: joined, with its address')
ok('[CURRENT]' in w.answer('LIST_NETWORKS') and f'ssid="{wifi.SSID}"' in (wroot/'mnt/data/wpa_supplicant.conf').read_text(), 'Wi-Fi: the network is saved as wpa_supplicant saves it')
ok(wifi.Wifi(wroot).connected, 'Wi-Fi: a saved network is joined again at the next start')
j = w.answer('ADD_NETWORK').strip(); w.answer(f'SET_NETWORK {j} ssid "Some other network"'); w.answer(f'SELECT_NETWORK {j}')
ok('COMPLETED' not in w.answer('STATUS'), 'Wi-Fi: a network that is not there is not joined')
w.answer(f'SELECT_NETWORK {i}'); w.answer('REMOVE_NETWORK all'); ok(not w.connected and w.answer('LIST_NETWORKS').count('\n') == 1, 'Wi-Fi: forgetting the network leaves it')
# over the socket itself, from a client bound where the firmware's binds (/tmp/wpa_ctrl_PID-N in the box)
w.start(); c = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); here = os.getcwd(); os.chdir(wroot)   # (short names: a socket's is limited)
try:
    c.bind('tmp/wpa_ctrl_1-1'); c.settimeout(3); c.sendto(b'PING', 'run/wpa_supplicant/wlan0')
    try: got = c.recv(100)
    except OSError as e: got = e
finally: os.chdir(here)
ok(got == b'PONG\n', f'Wi-Fi: answers on its socket, to the socket that asked: {got!r}')
ok((wroot/'run/wpa_supplicant/wlan0').is_socket(), "Wi-Fi: its socket is where the firmware looks, through the box's own links")
w.stop(); c.close(); ok(not (wroot/'run/wpa_supplicant/wlan0').exists(), 'Wi-Fi: and gone when it stops')

# the screen's answers for programs the shim cannot be loaded into
import fbdev, struct
ok(len(fbdev.Screen._fix()) == 80 and len(fbdev._filter()) % 8 == 0, "screen: the panel's fixed description is the size this PC's kernel gives it")
v = struct.unpack('<40I', fbdev.Screen._var(types.SimpleNamespace(yoffset=375)))
ok(v[:8] == (320, 375, 320, 750, 0, 375, 32, 0), 'screen: 320x375, two pages, 32 bits a pixel')
# the Q2's memory, as the firmware's programs are told it
m = dict(l.replace(' kB', '').split(':') for l in q2emu.meminfo(30).splitlines())
ok(int(m['MemTotal']) == 117864 and int(m['SwapTotal']) == 0
   and int(m['MemAvailable']) == int(m['MemTotal']) - (30 + q2emu.Q2_KERNEL_USE_MB) * 1024,
   f"meminfo: a real Q2's MemTotal, no swap, and what its programs hold taken off: {m['MemTotal'].strip()} / {m['MemAvailable'].strip()} kB")
ok('MemAvailable:          0 kB' in q2emu.meminfo(500), 'meminfo: never less than nothing left')
ok(q2emu.box_programs(tmp) == [], 'memory: only the programs of a box are counted')
# measuring mode: the instructions each of the firmware's programs counted, added up
import struct as _s
box = types.SimpleNamespace(root=tmp/'countroot', _seen_work={}, _work_gone=0); folder = box.root/'tmp/q2emu/work'; folder.mkdir(parents=True)
ok(q2emu.Q2._count(box) is None, 'work: nothing counted, nothing said')
(folder/'10.0').write_bytes(_s.pack('<Q', 1_000_000)); (folder/'11.0').write_bytes(_s.pack('<Q', 250))
ok(q2emu.Q2._count(box) == 1_000_250, "work: the programs' counts are added up")
time.sleep(0.02); (folder/'10.0').write_bytes(_s.pack('<Q', 3_000_000)); (folder/'11.0').unlink()
ok(q2emu.Q2._count(box) == 3_000_250, 'work: a count that grew is read again, and a program that is gone keeps what it ran')
ok([g._count_words(n) for n in (999, 28_800_000, 1_476_945_006)] == ['999', '28.8 M', '1.48 G'], 'work: said as 28.8 M, 1.48 G')
print('done')
