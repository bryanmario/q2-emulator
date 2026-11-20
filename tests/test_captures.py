"""Batch 2/3 checks without root: a real video through ffmpeg, screenshots, window sizes, touch marks,
the steps file, remembered preferences, and pictures of the redesigned window."""
import json, os, pathlib, subprocess, sys, tempfile, time, types
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
FIX = __import__('pathlib').Path(__file__).resolve().parent / 'fixtures'
import q2emu, emulator_gui as g
from PIL import Image, ImageChops

SHOTS = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else __file__).resolve().parent / 'out' if len(sys.argv) < 2 else pathlib.Path(sys.argv[1]); SHOTS.mkdir(parents=True, exist_ok=True)
ok = lambda c, what: print(('PASS ' if c else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp())

def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries',
                          'stream=width,height,nb_read_frames,r_frame_rate:format=duration', '-of', 'json', str(path)],
                         capture_output=True, text=True).stdout
    j = json.loads(out); s = j['streams'][0]
    return s['width'], s['height'], int(s['nb_read_frames']), float(j['format']['duration'])

# -- the recorder by itself: frames only when the picture changes, 3 s in all --
rec = g.Recorder(tmp/'plain.mp4', g.DEVICE_SIZE)
for i, pause in enumerate((0.2, 1.3, 0.1, 1.4)):
    rec.write(Image.new('RGB', g.DEVICE_SIZE, (40 * i, 80, 120))); time.sleep(pause)
err = rec.stop()
w, h, n, d = probe(tmp/'plain.mp4')
ok(err is None and (w, h) == (870, 1328), f'video written at twice the size: {w}x{h}, error {err}')
ok(2.9 <= d <= 3.3 and 58 <= n <= 66, f'video keeps real time: {d:.2f}s, {n} frames for 3.0s of recording')

ok(str(g.default_captures_dir()).endswith('/Videos/Q2 Emulator') and str(g.default_captures_dir()).startswith('/mnt/c/'),
   f'default captures folder: {g.default_captures_dir()} = {g.shown_path(g.default_captures_dir())}')

# -- the window, with a stand-in firmware --
HOME = Image.open(FIX/'screen-home.png').convert('RGB').resize(g.SCREEN_SIZE)
SETTINGS = Image.open(FIX/'screen-other.png').convert('RGB').resize(g.SCREEN_SIZE)
class FakeProc:
    returncode = None
    def poll(s): return s.returncode
class FakeQ2:
    started = lambda s: True; screen_off = lambda s: getattr(s, 'off', False)
    def __init__(s, r, l): s.proc = FakeProc(); s.got = []; s.pic = HOME
    def stop(s): s.proc.returncode = -9
    def frame(s): return s.pic
    def key(s, n): s.got.append(('key', n))
    def wheel(s, t): s.got.append(('wheel', t))
    def touch(s, x, y, down=True, first=False):
        s.got.append(('touch', x, y, down, first))
        if first: s.pic = SETTINGS if s.pic is HOME else HOME   # a touch changes the screen
g.unpack = lambda fw, r: ((r/'release/bin').mkdir(parents=True), (r/'mnt/data').mkdir(parents=True))
g._patch_serial_check = lambda p: None; g.build_shim = lambda r, w: None; g.prepare = lambda *a, **k: None; g.Q2 = FakeQ2
fw = tmp/'dev-test'/'update.tar'; fw.parent.mkdir(); fw.write_bytes(b'firmware')
work = tmp/'w'; caps = tmp/'captures'
g.Settings(work).set('captures', str(caps))
gui = g.EmulatorGUI(work=work)
win = gui.window
E = lambda x, y: types.SimpleNamespace(x=x, y=y)
def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        win.update(); time.sleep(0.01)
def shot(name):
    pump(0.25)
    subprocess.run(['import', '-window', str(win.winfo_id()), str(SHOTS/f'{name}.png')], capture_output=True)

pump(0.4); shot('b2_1_idle')
gui._load_firmware(fw); pump(1.0)
ok(gui.runner.state == 'running' and 'Running dev-test/update.tar' in gui.status.cget('text'), 'stand-in firmware running')
shot('b2_2_running')

# screenshots: with the player, then the screen alone
gui._screenshot(); pump(0.6)
gui._device_var.set(False); time.sleep(1.1); gui._screenshot(); pump(0.6)
pngs = sorted(caps.glob('*.png'))
sizes = [Image.open(p).size for p in pngs]
ok(sizes == [g.DEVICE_SIZE, g.SCREEN_SIZE], f'screenshots: with the player {sizes[:1]}, screen alone {sizes[1:]}')
ok('Screenshot saved' in gui.status.cget('text'), f'status after screenshot: {gui.status.cget("text")}')
ok(not ImageChops.difference(Image.open(pngs[1]).convert('RGB'), HOME).getbbox(), 'screen-alone screenshot is exactly the firmware frame')
gui._device_var.set(True)

# touch marks and button flashes show on the device image
before = gui._shown
gui._on_press(E(30 + 100, 27 + 150)); pump(0.15)
marks = gui._marks()
ok(marks and marks[0][0] == 'touch' and marks[0][1:3] == (130, 177), f'touch mark where the mouse is: {marks}')
gui._on_release(E(130, 177)); pump(0.4)
ok(gui._marks() == (), 'touch mark fades after release')
gui._key('centre'); win.update()
ok(gui._marks() == (('button', *g.BUTTON_AT['center']),), 'centre button lights on Enter')
shot('b2_3_button_lit'); pump(0.3)

# recording: 3.2 s with a key, a wheel turn, a tap and a swipe
ok(not gui._steps_var.get(), 'saving the clicks with a video is off unless asked for')
gui._steps_var.set(True)
gui._record(); rec_t0 = time.time(); pump(0.5)
ok(gui.recorder is not None and 'Stop' in gui._rec_btn.cget('text'), f'recording: button reads {gui._rec_btn.cget("text")!r}')
gui._key('return'); pump(0.5)
gui._wheel(2); pump(0.4)
gui._on_press(E(110, 320)); pump(0.1); gui._on_release(E(110, 320)); pump(0.6)       # tap at screen (80, 293)
shot('b2_4_recording')
gui._on_press(E(230, 300)); pump(0.05); gui._on_drag(E(230, 250)); pump(0.05); gui._on_drag(E(230, 150)); pump(0.05)
gui._on_release(E(230, 150)); pump(0.7)                                             # swipe up
rec_len = time.time() - rec_t0; gui._record()
end = time.time() + 15
while time.time() < end and 'Video saved' not in gui.status.cget('text'): pump(0.1)   # saved on a worker thread
mp4 = sorted(caps.glob('*.mp4'))
ok(len(mp4) == 1 and 'Video saved' in gui.status.cget('text'), f'status after recording: {gui.status.cget("text")}')
w, h, n, d = probe(mp4[0])
ok((w, h) == (870, 1328) and abs(d - rec_len) <= 0.5, f'recorded video: {w}x{h}, {d:.2f}s for {rec_len:.2f}s of recording, {n} frames')
steps = mp4[0].with_suffix('.steps.txt').read_text()
print('   steps file:', steps.replace('\n', ' '))
ok('key return' in steps and 'wheel 2' in steps and 'tap 80 293' in steps and 'swipe 200 273 200 123' in steps, 'steps file has the key, wheel, tap and swipe')
# the steps file must be a valid q2emu --script
class Replay:
    def __init__(s): s.did = []
    key = lambda s, n, *a: s.did.append('key'); wheel = lambda s, n: s.did.append('wheel')
    tap = lambda s, *a: s.did.append('tap'); swipe = lambda s, *a: s.did.append('swipe')
real_sleep = time.sleep; q2emu.time.sleep = lambda s: None
rp = Replay(); q2emu.Q2.script(rp, steps); q2emu.time.sleep = real_sleep
ok(rp.did == ['key', 'wheel', 'tap', 'swipe'], f'steps file replays through q2emu --script: {rp.did}')
# a frame from the middle of the video shows the device, not black
subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', '1.5', '-i', str(mp4[0]), '-frames:v', '1', str(SHOTS/'b2_video_frame.png')])
fr = Image.open(SHOTS/'b2_video_frame.png').convert('RGB')
ok(fr.size == (870, 1328) and len(fr.getcolors(100000) or range(999)) > 50, 'a frame from the video shows the player')

# a saved capture is announced in the window, with where it went
ok(gui.overlay.is_open and gui.overlay.kind == 'note', 'a note in the window says the video was saved, with its path')
gui.overlay.close()

# the window can take any size: the player fits the room it has, and clicks still land on the same screen point
got = gui.runner.q2.got; got.clear()
w0, h0 = win.winfo_width(), win.winfo_height()
for dw, dh in ((0, 0), (220, 300), (-90, -260), (520, 0)):
    win.geometry(f'{w0 + dw}x{h0 + dh}'); pump(0.7)
    s, (ox, oy) = gui.scale, gui.origin
    cw, ch, (vw, vh) = gui.canvas.winfo_width(), gui.canvas.winfo_height(), gui.view_size
    fits = vw <= cw and vh <= ch and (cw - vw <= 1 or ch - vh <= 1) and abs(vw / vh - 435 / 664) < 0.01
    gui._on_press(E(ox + (30 + 100.5) * s, oy + (27 + 150.5) * s)); gui._on_release(E(0, 0)); pump(0.15)
    first = [t for t in got if t[0] == 'touch' and t[4]][-1]
    ok(fits and first[1:3] == (100, 150), f'window {w0 + dw}x{h0 + dh}: player {vw}x{vh} fits its {cw}x{ch} space, click lands at {first[1:3]}')
    if dw: shot(f'b2_5_window_{w0 + dw}x{h0 + dh}')
buttons = sum(b.widget.winfo_width() for b in gui._tiles) + gui.GAP * 4
ok(buttons <= win.winfo_width() - 20 and buttons <= gui.CONTROLS_MAX, f'the buttons stay inside the window: {buttons}px of {win.winfo_width()}')

# a spin of the mouse wheel: turns that arrive while the firmware is still taking one are made as one stroke
got.clear(); q2 = gui.runner.q2; quick = q2.wheel
q2.wheel = lambda t: (time.sleep(0.2), quick(t))[1]   # as slow as the real stroke
for _ in range(8): gui._wheel(1); pump(0.02)
pump(1.2); q2.wheel = quick
turns = [t[1] for t in got if t[0] == 'wheel']
ok(sum(turns) == 8 and len(turns) <= 3, f'8 quick turns of the wheel reach the firmware as {len(turns)} strokes, not 8: {turns}')
got.clear(); q2.wheel = lambda t: (time.sleep(0.2), quick(t))[1]
for _ in range(30): gui._wheel(1)
pump(0.1); gui._key('return'); pump(1.5); q2.wheel = quick
turns = [t[1] for t in got if t[0] == 'wheel']
ok(sum(turns) <= 12 and got[-1] == ('key', 'return'), f'a button pressed during a long spin cuts the rest of it off: {sum(turns)} of 30 turns made, then {got[-1]}')

# the firmware's screen-off timer: shown as off, not as a picture that looks frozen; any input only wakes it
got.clear(); gui._notice = None; gui.runner.q2.off = True; pump(0.3)
ok(gui._asleep and 'screen off' in gui.status.cget('text') and not ImageChops.difference(gui._clean, g.SLEEP_SCREEN).getbbox(),
   f'the player switching its screen off shows as off: {gui.status.cget("text")[:60]}')
gui._wheel(1); gui._on_press(E(130, 177)); gui._on_release(E(0, 0)); gui._key('play'); pump(0.3)
ok(got == [('key', 'centre')], f'while it is off, a wheel turn, a click and a key only press its power key, once: {got}')
gui.runner.q2.off = False; pump(0.3)
ok(not gui._asleep and 'Running' in gui.status.cget('text'), 'and it is back when the firmware switches the screen on')
time.sleep(1.0); got.clear()
# everything the window shows is drawn inside it: no second window, nothing that can hide or hold the mouse
def extra_windows():
    return [w for w in win.winfo_children() if w.winfo_class() in ('Toplevel', 'Menu')]
got.clear()
gui._more_popup(); pump(0.3); shot('b3_menu')
ok(gui.overlay.kind == 'menu' and not extra_windows(), 'More opens as a panel inside the window')
gui._on_press(types.SimpleNamespace(x=130, y=177, widget=gui.canvas)); gui._on_release(E(0, 0)); pump(0.1)
ok(not gui.overlay.is_open and not got, 'a click outside the menu closes it, and is not also a touch on the player')
gui._help(); pump(0.3); shot('b3_help')
ok(gui.overlay.kind == 'help' and not extra_windows(), 'Help opens as a panel inside the window')
asked = []
gui._ask('A question', 'Go ahead?', 'Yes', lambda: asked.append('yes')); pump(0.3); shot('b3_question')
yes = gui.overlay._keep[-1][0]   # buttons are made right to left: the one that goes ahead first
yes.command(); pump(0.1)
ok(asked == ['yes'] and not gui.overlay.is_open and not extra_windows(), 'a question is a panel too; its button answers and closes it')
gui._power_btn._enter(None); gui._power_btn._tip_show(); pump(0.1)
ok(gui._power_btn._tipwin is not None and not extra_windows(), 'a tip is a label inside the window')
gui._power_btn._leave(None)
gui._agent_var.set(True); gui._toggle_agent(); pump(0.2)
ok('AI agent control enabled' in gui.status.cget('text') and gui.door is not None, f'agent control says so plainly: {gui.status.cget("text")}')
gui._agent_var.set(False); gui._toggle_agent(); pump(0.2)
ok('AI agent control disabled' in gui.status.cget('text') and gui.door is None, 'and can be turned off')

win.geometry(f'{w0 + 60}x{h0 + 40}'); gui._pick_shell('Silver'); pump(1.3); shot('b2_6_silver')
gui.runner.close(); win.destroy()

st = g.Settings(work).data
ok(st.get('window') == [w0 + 60, h0 + 40] and st.get('shell') == 'Silver', f'preferences saved: {st}')
gui2 = g.EmulatorGUI(work=work)
end = time.time() + 0.8
while time.time() < end: gui2.window.update(); time.sleep(0.01)
ok((gui2.window.winfo_width(), gui2.window.winfo_height()) == (w0 + 60, h0 + 40) and gui2.shell == 'Silver', 'next start: same window size and shell color')
gui2.runner.close(); gui2.window.destroy()
print('captures:', sorted(p.name for p in caps.iterdir()))
# the smoothing is the shell's alone: the screen is the firmware's own pixels
from device_view import DeviceView
view = DeviceView('#000000'); inner = (45, 45, 330, 275)   # the screen, clear of its rounded corners
pic, at = view.screen(g.DEVICE_SIZE, 'Orange', HOME)
ok(pic.size == g.SCREEN_SIZE and at == g.SCREEN_AT and not ImageChops.difference(pic.crop(inner), HOME.crop(inner)).getbbox(),
   'at its own size the screen shows the firmware\'s pixels exactly, one for one')
pic2, _ = view.screen((g.DEVICE_SIZE[0] * 2, g.DEVICE_SIZE[1] * 2), 'Orange', HOME)
twice = HOME.resize((750, 640), Image.NEAREST); inner2 = tuple(v * 2 for v in inner)
ok(pic2.size == (750, 640) and not ImageChops.difference(pic2.crop(inner2), twice.crop(inner2)).getbbox(),
   'at twice the size each firmware pixel is a sharp 2x2 block, not blended')