"""Batch 1 checks without root: the runner's logic with a stand-in firmware, taking over from another
emulator (real processes), and the real Tk window driven through each state, with screenshots."""
import os, pathlib, signal, subprocess, sys, tempfile, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
FIX = __import__('pathlib').Path(__file__).resolve().parent / 'fixtures'
import q2emu, emulator_gui as g
from PIL import Image

SHOTS = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else __file__).resolve().parent / 'out' if len(sys.argv) < 2 else pathlib.Path(sys.argv[1]); SHOTS.mkdir(parents=True, exist_ok=True)
ok = lambda c, what: print(('PASS ' if c else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp())

# -- friendly errors --
for raw, want in (('qemu-mipsel-static is missing: set Q2EMU_QEMU', "QEMU program wasn't found"),
                  ('the serial-length gate ... differs from the ones the emulator knows', "isn't supported"),
                  ("filename 'recovery-update/rootfs.squashfs' not found", "doesn't look like Q2 firmware"),
                  ('StopIteration', "doesn't look like Q2 firmware"),
                  ('/x/mnt/mmc is still mounted; unmount it and retry', "couldn't be released"),
                  ('something odd', 'Something went wrong')):
    ok(want in g.friendly(raw)[0], f'friendly: {raw[:40]!r} -> {g.friendly(raw)[0]!r}')

# -- stand-in firmware --
HOME_FRAME = Image.open(FIX/'screen-home.png').convert('RGB').resize(g.SCREEN_SIZE)
class FakeProc:
    def __init__(s): s.returncode = None
    def poll(s): return s.returncode
class FakeQ2:
    started = lambda s: True; screen_off = lambda s: getattr(s, 'off', False)
    def __init__(s, r, l): s.proc = FakeProc(); s.t0 = time.time()
    def stop(s): s.proc.returncode = s.proc.returncode if s.proc.returncode is not None else -9
    def frame(s):  # black for 0.6 s, as the real one is while booting
        return Image.new('RGB', g.SCREEN_SIZE) if time.time() - s.t0 < 0.6 else HOME_FRAME
    def key(s, n, ms=80): pass
    def press(s, n, down=True): pass
    def touch(s, *a, **k): pass
    def wheel(s, t): pass
unpacks = []
def fake_unpack(fw, r):
    time.sleep(0.4); unpacks.append(fw.name); (r/'release/bin').mkdir(parents=True); (r/'mnt/data').mkdir(parents=True)
g.unpack = fake_unpack; g._patch_serial_check = lambda p: time.sleep(0.2); g.build_shim = lambda r, w: None
g.prepare = lambda *a, **k: None; g.Q2 = FakeQ2
fw = tmp/'dev-test'/'update.tar'; fw.parent.mkdir(); fw.write_bytes(b'firmware')
ok(g.firmware_name(fw) == 'dev-test/update.tar', 'firmware shown with its folder name')

# -- runner: fresh start erases saved settings; same file keeps them --
r = g.FirmwareRunner(tmp/'w1')
def wait(rr=r):
    while rr.loading: time.sleep(0.02)
r.load(fw); wait(); (r.root/'mnt/data/config.ini').write_text('x')
r.load(fw); wait(); ok((r.root/'mnt/data/config.ini').exists(), 'same firmware keeps settings')
r.load(fw, fresh=True); wait(); ok(not (r.root/'mnt/data/config.ini').exists() and r.state == 'running', 'start fresh erases them')
ok(r.power_off() and r.state == 'off' and r.q2 is None, 'Power off stops the firmware')
r.close()

# -- another emulator holding the folder: busy, then take over (real processes) --
w2 = tmp/'w2'; (w2/'rootfs').mkdir(parents=True)
holder = subprocess.Popen([sys.executable, '-c', f'import sys; sys.path.insert(0, "{q2emu.HERE}"); import q2emu, pathlib, time; '
                           f'l = q2emu.lock(pathlib.Path("{w2}")); time.sleep(600)'])
box = subprocess.Popen(['unshare', '--user', '--map-root-user', '--pid', '--fork', 'sh', '-c',
                        f'sleep 600 # {w2}/rootfs'], start_new_session=True)
time.sleep(1)
holders, boxes = g.others(w2)
ok(holder.pid in holders and box.pid in boxes, f'finds the other emulator and its sandbox: {holders} {boxes}')
r2 = g.FirmwareRunner(w2)
r2.load(fw); wait(r2); ok(r2.state == 'busy', f'second emulator reports busy: {r2.message}')
r2.load(fw, take_over=True); wait(r2)
ok(holder.poll() is not None and box.poll() is not None, 'take over closes the other emulator and its sandbox')
ok(r2.state == 'running', 'and starts here')
r2.close()

# -- a firmware left running by a window that died (no one holds the folder): stopped at the next start --
w4 = tmp/'w4'; (w4/'rootfs').mkdir(parents=True)
box = subprocess.Popen(['unshare', '--user', '--map-root-user', '--pid', '--fork', 'sh', '-c',
                        f'sleep 600 # {w4}/rootfs'], start_new_session=True)
time.sleep(1)
r4 = g.FirmwareRunner(w4)
r4.load(fw); wait(r4)
ok(box.poll() is not None and r4.state == 'running', f'a leftover firmware is stopped before a new one starts: {r4.state} {r4.message}')
r4.close()
# -- the real window, driven through its states --
w3 = tmp/'w3'
gui = g.EmulatorGUI(work=w3)
win = gui.window
def shot(name):
    win.update(); time.sleep(0.15); win.update()
    out = SHOTS/f'{name}.png'
    subprocess.run(['import', '-window', win.winfo_id().__str__(), str(out)], capture_output=True)
    if not out.exists():  # fall back to the device image alone
        gui.ImageTk  # noqa
    return out
seq = []
def step(i=0):
    r = gui.runner
    s = gui.status.cget('text')
    if i == 0:
        seq.append(('idle', s, str(gui._power_btn.cget('state')))); shot('1_idle')
        gui._load_firmware(fw)
    elif i == 1:
        seq.append(('loading', s)); shot('2_loading')
    elif i == 2:
        seq.append(('booting', s, gui._restart_btn.cget('state'))); shot('3_booting')
    elif i == 3:
        seq.append(('running', s, gui._power_btn.cget('text'))); shot('4_running')
        gui._power()
    elif i == 4:
        seq.append(('off', s, gui._power_btn.cget('text'))); shot('5_off')
        r.state, r.message, r.hint = 'error', *g.friendly("filename 'recovery-update/rootfs.squashfs' not found")
    elif i == 5:
        seq.append(('error', s)); shot('6_error')
        r.state = 'idle'
        gui.runner.sd_card = (tmp/'Music'); gui._sd_changed()
        gui._shell_var.set('Champagne Gold'); gui._set_shell()
    elif i == 6:
        seq.append(('sd', gui._sd_btn.cget('text'))); shot('7_sd_and_shell')
        gui.runner.close(); win.destroy(); return
    delays = {0: 300, 1: 500, 2: 900, 3: 400, 4: 400, 5: 400}
    win.after(delays[i], step, i + 1)
win.after(400, step)
win.mainloop()
for row in seq:
    print('  ', row)
ok(seq[0][2] == 'disabled' and 'Click Firmware to load' in seq[0][1], 'idle: Power on disabled until a firmware is known')
ok(seq[1][1].startswith(('Checking', 'Unpacking', 'Preparing', 'Starting')), 'loading shows its step')
ok('Booting' in seq[2][1], 'booting status while the screen is still black')
ok(seq[3][1].startswith('Running dev-test/update.tar') and seq[3][2] == 'Power Off', 'running status + Power off button')
ok('powered off' in seq[4][1] and seq[4][2] == 'Power On', 'Power off works from the button')
ok("doesn't look like Q2 firmware" in seq[5][1] and 'Choose an update.tar' in seq[5][1], 'error says what to do')
ok(seq[6][1] == 'Music', 'SD button shows the mounted folder')
st = g.Settings(w3)
ok(st.get('firmware') == str(fw) and st.get('sd_card') == str(tmp/'Music') and st.get('shell') != 'Orange',
   f'remembered: {st.data}')
gui2 = g.EmulatorGUI(work=w3)
ok(gui2.runner.firmware == fw and gui2.runner.state in ('loading', 'running'), f'next start loads the last firmware by itself: {gui2.runner.state}')
gui2.runner.close(); gui2.window.destroy()
print('screenshots in', SHOTS)
