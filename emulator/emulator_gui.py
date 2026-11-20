#!/usr/bin/env python3
"""Q2 Emulator - desktop GUI with device shell and live firmware display.

Wraps q2emu's QEMU engine in the visual device mockup from simulator_device,
giving a realistic Q2 experience: the actual firmware runs on the screen,
and clicks on the wheel/screen/buttons drive the real input devices.

  sudo python3 emulator/emulator_gui.py --firmware path/to/update.tar
  sudo python3 emulator/emulator_gui.py   # opens idle, load firmware from GUI

Threads: Tk runs on the main thread only. Loading runs on a worker thread that
only sets FirmwareRunner's fields; the GUI polls them in _refresh. Input goes
through one queue drained by one thread, so events reach the firmware in order.
A recording's frames go through a queue to one thread that feeds ffmpeg.
"""
import argparse
import hashlib
import json
import os
import pathlib
import queue
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw, ImageFont
from simulator_device import (
    CENTRE, DEFAULT_SHELL, DETENT, DEVICE_SIZE, SCREEN_AT, SCREEN_SIZE,
    SHELL_FINISHES, WHEEL, device_frame, wheel_angle, wheel_button, wheel_delta,
)
import agent_door
import controls
from device_view import DeviceView
from q2emu import (
    Q2, Q2_INSNS_PER_S, Q2_RAM_MB, build_shim, can_count, lock, prepare, remove_rootfs, unmount, unpack, _patch_serial_check,
)

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
WHEEL_KEYS = {'center': 'centre', 'up': 'return', 'down': 'play', 'left': 'prev', 'right': 'next'}
KEY_BUTTONS = {key: button for button, key in WHEEL_KEYS.items()}
BUTTON_AT = {  # where a pressed wheel button lights up: x, y, radius in the device image
    'center': (WHEEL[0], WHEEL[1], CENTRE - 3),
    'up': (WHEEL[0], WHEEL[1] - 86, 24), 'down': (WHEEL[0], WHEEL[1] + 85, 24),
    'left': (WHEEL[0] - 85, WHEEL[1], 24), 'right': (WHEEL[0] + 85, WHEEL[1], 24),
}
BUSY = 'another q2emu is already running'  # q2emu.lock()'s message
POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
EXPLORER = '/mnt/c/Windows/explorer.exe'
HELP = (  # More > Help: (heading, ((name, what it does), ...))
    ('Buttons', (
        ('Power', 'Switches the player on or off. Its settings are kept.'),
        ('Restart', 'Off and on again.'),
        ('Screenshot', 'Saves a picture (F12).'),
        ('Record', 'Records a video until you press it again (F9).'),
        ('Firmware', 'Opens an update.tar or a firmware ZIP.'),
        ('SD card', 'A music folder that the player sees as its card.'),
    )),
    ('The player', (
        ('Screen', 'Click to touch it, drag to swipe.'),
        ('Wheel', 'Click its center or its four marks. Drag around it, or roll the mouse wheel, to scroll.'),
    )),
    ('Keyboard', (
        ('Enter', 'Center button'),
        ('Esc, Backspace', 'Back'),
        ('Space', 'Play / Pause'),
        ('Left, Right', 'Previous / Next'),
        ('Up, Down', 'Turn the wheel'),
    )),
)
SD_HELP = ('Choose any folder with music in it (MP3, FLAC and the like; folders inside it are fine). The player '
           'restarts and finds it as its SD card: open Folder on the player to browse and play it. Nothing is '
           'copied. The player reads your folder itself, and may save small files of its own in it.')


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def firmware_name(path):
    """A name to show for a firmware file: its folder too when the file is just update.tar."""
    path = pathlib.Path(path)
    return f'{path.parent.name}/{path.name}' if path.name == 'update.tar' and path.parent.name else path.name


def friendly(error):
    """(what happened, what to do) for a load failure, in plain words."""
    e = error
    if 'qemu-mipsel-static is missing' in e:
        return ("The emulator's QEMU program wasn't found.",
                'Put the QEMU 11.1.3 build at /opt/q2-qemu/bin, or set Q2EMU_QEMU.')
    if 'differs from the ones the emulator knows' in e or 'no home_page_init' in e:
        return ("This firmware isn't supported yet.",
                'The emulator knows Shanling V1.36 and the Q2 Pod builds based on it.')
    if 'still mounted' in e:
        return ("The SD card folder couldn't be released.",
                'Use More > Eject SD card, then try again. If that fails, close the emulator and open it again.')
    if 'No such file or directory' in e and ('.tar' in e or '.zip' in e):
        return ("The firmware file wasn't found.", 'It may have been moved or renamed. Open it again.')
    if ('rootfs.squashfs' in e or 'StopIteration' in e or 'could not be opened' in e
            or 'not a gzip' in e or 'truncated' in e):
        return ("This file doesn't look like Q2 firmware.",
                'Choose an update.tar, or a firmware ZIP that contains one.')
    return ('Something went wrong while loading.', 'Details: ' + e)


def _font(size):
    return controls.font(size)  # found through fontconfig: its place differs from one Linux to another


def windows_dialog(kind, title, start=None):
    """Ask with Windows' own file or folder dialog (under WSL). Returns the chosen path as Linux names
    it, '' if cancelled, None if Windows' dialogs cannot be used here. Blocks: call it off the Tk thread."""
    if not os.path.exists(POWERSHELL):
        return None
    quote = lambda s: "'" + str(s).replace("'", "''") + "'"
    start = shown_path(start) if start and os.path.isdir(start) else ''
    if kind == 'file':
        ask = ("$d = New-Object System.Windows.Forms.OpenFileDialog; $d.Title = " + quote(title) + "; "
               "$d.Filter = 'Q2 firmware (*.tar;*.zip)|*.tar;*.zip|All files (*.*)|*.*'; "
               + (f"$d.InitialDirectory = {quote(start)}; " if start else '') +
               "if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.FileName) }")
    else:
        ask = ("$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = " + quote(title) + "; "
               + (f"$d.SelectedPath = {quote(start)}; " if start else '') +
               "if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
              "$f = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; " + ask)
    try:
        import base64
        out = subprocess.run([POWERSHELL, '-NoProfile', '-STA', '-EncodedCommand',
                              base64.b64encode(script.encode('utf-16-le')).decode()],
                             capture_output=True, cwd='/mnt/c').stdout.decode('utf-8', 'replace').strip()
        if not out:
            return ''
        return subprocess.run(['wslpath', '-u', out], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _message_screen(lines, bg='#0a090b'):
    """A plain screen with centred lines of (text, colour, size); long text is wrapped."""
    rows = []
    for text, colour, size in lines:
        for part in textwrap.wrap(text, width=max(10, 560 // size)) or ['']:
            rows.append((part, colour, size))
    img = Image.new('RGB', SCREEN_SIZE, bg)
    draw = ImageDraw.Draw(img)
    cx, cy = SCREEN_SIZE[0] // 2, SCREEN_SIZE[1] // 2
    y = cy - 11 * (len(rows) - 1)
    for text, colour, size in rows:
        draw.text((cx, y), text, fill=colour, font=_font(size), anchor='mm')
        y += 22
    return img


IDLE_SCREEN = _message_screen([
    ('Q2 Emulator', '#c0c0c0', 20),
    ('Click "Open firmware" to load', '#666666', 13),
    ('an update.tar or firmware ZIP', '#666666', 13),
])
SLEEP_SCREEN = _message_screen([('Screen off', '#9a9a9a', 16),
                                ('The player switched its screen off.', '#666666', 12),
                                ('Click it or press a key to wake it.', '#666666', 12)], bg='#000000')
OFF_SCREEN = _message_screen([('Powered off', '#c0c0c0', 16),
                              ('Press Power on to start again', '#666666', 13)])


_OUTLINE = Image.new('L', DEVICE_SIZE)  # white where the player is, black around it
ImageDraw.Draw(_OUTLINE).rounded_rectangle((5, 1, 429, 659), 72, fill=255)


def player(screen, shell, backdrop=controls.BG):
    """The picture of the player showing screen, on the window's colour; with backdrop None, on
    nothing at all (transparent), for a screenshot."""
    device = device_frame(screen, shell)
    if backdrop is None:
        device = device.convert('RGBA')
        device.putalpha(_OUTLINE)
        return device
    return Image.composite(device, Image.new('RGB', DEVICE_SIZE, backdrop), _OUTLINE)


def draw_marks(img, marks, offset=(0, 0)):
    """img with the touch point and pressed wheel buttons lit: marks are (kind, x, y, radius) in the
    device image's coordinates, offset is where img's corner sits in it."""
    if not marks:
        return img
    base = img.convert('RGBA')
    over = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(over)
    for kind, x, y, r in marks:
        box = (x - offset[0] - r, y - offset[1] - r, x - offset[0] + r, y - offset[1] + r)
        if kind == 'touch':
            draw.ellipse(box, fill=(255, 255, 255, 70), outline=(255, 255, 255, 210), width=2)
        else:
            draw.ellipse(box, fill=(255, 255, 255, 70))
    return Image.alpha_composite(base, over).convert('RGB')


# -- Files the emulator saves for the user --

def _hand_over(path):
    """Files made as root inside the Linux home go to the user who ran sudo."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if uid and gid and not str(path).startswith('/mnt/'):
        try:
            os.chown(path, int(uid), int(gid))
        except OSError:
            pass


def default_captures_dir():
    """Windows' own Videos folder when this runs under WSL, else the home folder; in "Q2 Emulator"."""
    try:
        out = subprocess.run([POWERSHELL, '-NoProfile', '-Command', "[Environment]::GetFolderPath('MyVideos')"],
                             capture_output=True, text=True, timeout=10, cwd='/mnt/c').stdout.strip()
        if out:
            linux = subprocess.run(['wslpath', '-u', out], capture_output=True, text=True, timeout=5).stdout.strip()
            if linux and os.path.isdir(linux):
                return pathlib.Path(linux) / 'Q2 Emulator'
    except (OSError, subprocess.SubprocessError):
        pass
    return pathlib.Path.home() / 'Q2 Emulator'


def shown_path(path):
    """The path as the user's file manager names it: Windows' form under WSL."""
    try:
        out = subprocess.run(['wslpath', '-w', str(path)], capture_output=True, text=True, timeout=5).stdout.strip()
        return out or str(path)
    except (OSError, subprocess.SubprocessError):
        return str(path)


def reveal_in_explorer(path):
    """Open the file's folder in Windows' Explorer with the file selected; True if Explorer was asked.
    Through PowerShell: Explorer wants /select,"C:\\a folder\\file" with the quotes around the path
    alone, which a program started from Linux cannot give it (the whole argument gets quoted, and
    Explorer then opens Documents)."""
    import base64
    shown = shown_path(path).replace("'", "''")
    script = "Start-Process explorer.exe -ArgumentList ('/select,\"' + '" + shown + "' + '\"')"
    try:
        done = subprocess.run([POWERSHELL, '-NoProfile', '-EncodedCommand',
                               base64.b64encode(script.encode('utf-16-le')).decode()],
                              capture_output=True, cwd='/mnt/c', timeout=30)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class Recorder:
    """A video of the pictures written to it, in real time.

    The recorder keeps the clock itself: the video has exactly FPS frames for every second since it
    started, each the picture that was current then. write() gives it a new picture, tick() (called
    often) lets time pass on an unchanged one. However slow ffmpeg is to start or to encode, the timing
    in the file is the timing of the writes; a picture's repeats are queued as a count, not as copies."""
    FPS = 20

    def __init__(self, path, size):
        self.path = pathlib.Path(path)
        self.started = time.time()
        self.failed = None
        self._picture = None   # the current picture's bytes
        self._sent = 0         # frames handed to ffmpeg so far
        self.proc = subprocess.Popen(
            ['ffmpeg', '-loglevel', 'error', '-y',
             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}', '-r', str(self.FPS), '-i', '-',
             '-vf', 'scale=iw*2:ih*2:flags=neighbor',  # twice the size, pixels kept sharp
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
             '-movflags', '+faststart', str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._frames = queue.Queue()
        self._pump = threading.Thread(target=self._feed, daemon=True)
        self._pump.start()

    @property
    def seconds(self):
        return time.time() - self.started

    def tick(self):
        """Time has passed: the current picture fills the frames up to now."""
        if self._picture is None:
            return
        due = int((time.time() - self._first) * self.FPS)
        if due > self._sent:
            self._frames.put((self._picture, due - self._sent))
            self._sent = due

    def write(self, img):
        if self._picture is None:
            self._first = time.time()  # the video's clock starts with its first picture
        self.tick()  # the picture before this one lasted until now
        self._picture = img.tobytes()

    def _feed(self):
        while True:
            item = self._frames.get()
            if item is None:
                break
            data, count = item
            try:
                for _ in range(count):
                    self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except OSError as e:
                self.failed = str(e)
                break
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def stop(self):
        """Finish the file. Returns None, or what went wrong."""
        self.tick()
        if self._picture is not None and not self._sent:
            self._frames.put((self._picture, 1))  # stopped at once: still one frame
        self._frames.put(None)
        self._pump.join(timeout=60)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return 'the video encoder did not finish'
        err = self.proc.stderr.read().decode(errors='replace').strip()
        if self.proc.returncode or self.failed:
            return err or self.failed or f'ffmpeg ended with status {self.proc.returncode}'
        _hand_over(self.path)
        return None


# -- Another emulator holding the work folder --

def others(work):
    """(processes holding work/lock, sandboxes running work/rootfs) other than this process."""
    lockfile, root, me = str(work / 'lock'), str(work / 'rootfs').encode(), os.getpid()
    holders, boxes = set(), set()
    for d in os.listdir('/proc'):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            if any(os.readlink(f'/proc/{d}/fd/{fd}') == lockfile for fd in os.listdir(f'/proc/{d}/fd')):
                holders.add(int(d))
        except OSError:
            pass
        try:
            cmd = open(f'/proc/{d}/cmdline', 'rb').read().split(b'\0')
            if cmd[0].endswith(b'unshare') and any(root in c for c in cmd):
                boxes.add(int(d))
        except (OSError, IndexError):
            pass
    return holders, boxes


def close_others(work):
    """End another emulator on work: its sandbox first (as Q2.stop does), then the program holding the lock."""
    holders, boxes = others(work)
    for group in boxes:
        for sig in (signal.SIGUSR2, signal.SIGKILL):
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                break
            if _gone(group, 3):
                break
    for pid in holders:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            if _gone(pid, 3):
                break
    mmc = work / 'rootfs/mnt/mmc'
    unmount(mmc)


def _gone(pid, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if not os.path.exists(f'/proc/{pid}'):
            return True
        time.sleep(0.1)
    return False


def _count_words(n):
    """A count of instructions as it is said: 412 M, 1.48 G."""
    for size, unit in ((1e9, 'G'), (1e6, 'M'), (1e3, 'k')):
        if n >= size:
            return f'{n / size:.3g} {unit}'
    return str(int(n))


def internet_tool():
    """slirp4netns, which carries the player's internet; None where it is not installed."""
    return shutil.which('slirp4netns')


def get_internet_tool():
    """(Worker thread.) Install slirp4netns into the emulator's private Linux, for a copy set up before
    the emulator had internet: its package manager fetches it once. True when it is there afterwards."""
    if not internet_tool() and shutil.which('apk'):
        subprocess.run(['apk', 'add', '--no-cache', '-q', 'slirp4netns'], capture_output=True, timeout=180)
    return bool(internet_tool())


class FirmwareRunner:
    """The firmware's lifecycle, independent of Tk.

    state is one of: idle, loading, running, off (powered off, by the firmware or the Power button),
    stopped (the firmware exited otherwise), busy (another emulator holds the folder), error.
    A Restart inside the firmware keeps it running. Only the load thread and stop()/close() change it;
    the GUI just reads it."""

    def __init__(self, work_dir=None):
        self.work = pathlib.Path(work_dir or pathlib.Path.home() / '.cache/q2emu')
        self.root = self.work / 'rootfs'
        self.q2 = None
        self.log = None
        self.sd_card = None
        self.internet = False     # the pretend Wi-Fi with this PC's internet behind it (wifi.py), from the next start
        self.measure = False      # count the instructions the firmware executes (q2emu.COUNT), from the next start
        self.firmware = None      # the firmware file last asked for
        self.state = 'idle'
        self.message = ''         # the current step, or what went wrong
        self.hint = ''            # with an error: what to do about it
        self._lock = None
        self._closing = False
        self._loader = None
        self._inputs = queue.Queue()
        threading.Thread(target=self._input_loop, daemon=True).start()

    @property
    def running(self):
        return self.q2 is not None and self.q2.proc.poll() is None

    @property
    def loading(self):
        return self._loader is not None and self._loader.is_alive()

    def poll(self):
        """Notice a firmware that has exited on its own."""
        if self.state == 'running' and not self.running:
            code = self.q2.proc.returncode if self.q2 else None
            self.stop()
            if code == 0:  # q2emu's start script: 0 is the firmware's own Power off
                self.state, self.message = 'off', 'The player powered off.'
            elif code == 97:  # q2emu's start script: three reboots within seconds of booting
                self.state = 'stopped'
                self.message = 'The firmware kept restarting right after it started, so the emulator stopped it.'
            else:
                self.state = 'stopped'
                self.message = f'The firmware stopped unexpectedly (exit status {code}).'
            self.hint = '' if code == 0 else f'Its log: {self.work / "demo.log"}'

    # -- Loading --

    def load(self, firmware_path, strace=False, fresh=False, take_over=False):
        """Start loading on a worker thread. False if a load is already under way.
        fresh: unpack again, dropping the settings this firmware saved. take_over: first end
        another emulator holding the work folder."""
        if self.loading or self._closing:
            return False
        self.firmware = pathlib.Path(firmware_path)
        self.state, self.message, self.hint = 'loading', 'Checking the firmware file...', ''
        self._loader = threading.Thread(
            target=self._load_thread, args=(self.firmware, strace, fresh, take_over), daemon=True)
        self._loader.start()
        return True

    def _load_thread(self, firmware_path, strace, fresh, take_over):
        try:
            self.stop()
            if take_over:
                self.message = 'Closing the other emulator...'
                close_others(self.work)
            if self._lock is None:  # before anything touches the rootfs; held until close()
                self._lock = lock(self.work)
            if others(self.work)[1]:  # the folder is ours, yet a firmware runs in it: one whose window died
                self.message = 'Stopping a firmware left running earlier...'
                close_others(self.work)
            self.message = 'Checking the firmware file...'
            # Keep the unpacked firmware, and with it the settings the firmware saved (language etc.),
            # while the same firmware file is loaded again. A different file, even a fork of the same
            # version, gets a clean unpack and so starts from its own first-boot setup.
            stamp = self.work / 'firmware.sha256'
            digest = _sha256(firmware_path)
            same = self.root.exists() and stamp.exists() and stamp.read_text().strip() == digest
            if self.root.exists() and (fresh or not same):
                stamp.unlink(missing_ok=True)
                remove_rootfs(self.root)
            if not self.root.exists():
                self.message = 'Unpacking the firmware (the first start of a file takes a little longer)...'
                unpack(firmware_path, self.root)
                stamp.write_text(digest + '\n')  # only once the unpack is complete
            self.message = 'Preparing the emulated player...'
            _patch_serial_check(self.root / 'release/bin/demo')
            build_shim(self.root, self.work)
            net = {'internet': True} if self.internet and internet_tool() else {}
            prepare(self.root, self.sd_card, strace, **net, **({'count': True} if self.measure and can_count() else {}))
            self.message = 'Starting the player...'
            self.log = open(self.work / 'demo.log', 'wb')
            self.q2 = Q2(self.root, self.log, **net)
            if self._closing:  # the window was closed while this was loading
                self.stop()
                return
            self.state = 'running'
        except (Exception, SystemExit) as e:
            # SystemExit too: q2emu's helpers sys.exit() on a missing tool (e.g. no QEMU found).
            # Uncaught, it would end this thread silently and leave the GUI on "Loading firmware..." forever.
            raw = str(e) or type(e).__name__
            print('q2emu load failed:', raw, file=sys.stderr, flush=True)
            self.stop()
            if BUSY in raw:
                self.state, self.message, self.hint = 'busy', 'Another emulator window is already running.', ''
            else:
                self.message, self.hint = friendly(raw)
                self.state = 'error'

    # -- Running --

    def frame(self):
        if not self.running:
            return None
        try:
            return self.q2.frame()
        except (OSError, ValueError):
            return None

    def started(self):
        """The firmware has shown a picture of its own: the boot's loading screen is over."""
        q2 = self.q2
        return bool(q2 and self.running and q2.started())

    def screen_off(self):
        """The firmware has switched its screen off (its idle timer); see Q2.screen_off."""
        q2 = self.q2
        return bool(q2 and self.running and q2.screen_off())

    def memory(self):
        """(MB the firmware's programs hold, [(program, MB), ...]) as last measured, or None; see Q2.memory."""
        q2 = self.q2
        used = getattr(q2, 'used', None) if q2 and self.running else None
        return used if used and used[1] else None

    def memory_peak(self):
        """The most memory() has been since the firmware started, or None."""
        q2 = self.q2
        peak = getattr(q2, 'peak', None) if q2 and self.running else None
        return peak if peak and peak[1] else None

    def instructions(self):
        """The instructions the firmware has executed since it was loaded, in measuring mode; else None."""
        q2 = self.q2
        return getattr(q2, 'work', None) if q2 and self.running else None

    def send(self, action):
        """Queue action(q2) for the input thread, if the firmware is running."""
        if self.running:
            self._inputs.put(action)

    def _input_loop(self):
        while True:
            action = self._inputs.get()
            q2 = self.q2
            if q2 is None or q2.proc.poll() is not None:
                continue  # input for a firmware that is gone
            try:
                action(q2)
            except Exception as e:  # whatever went wrong with one input, the next still gets through
                print('q2emu input failed:', e, file=sys.stderr, flush=True)

    # -- Ending --

    def stop(self):
        """Stop the firmware, if any; the work folder stays locked."""
        q2, self.q2 = self.q2, None
        if q2:
            q2.stop()
        if self.log:
            self.log.close()
            self.log = None

    def power_off(self):
        """The Power button: stop the firmware, as if the player were switched off."""
        if self.loading:
            return False
        self.stop()
        self.state, self.message, self.hint = 'off', 'The player is powered off.', ''
        return True

    def close(self):
        """Stop for good: the window is closing."""
        self._closing = True
        self.stop()
        if self._lock and not self.loading:  # a loader still running keeps the folder locked
            self._lock.close()
            self._lock = None


class Settings:
    """What the window remembers between runs (work/gui.json): the last firmware, the SD folder,
    the shell color, the window's size, where captures go and how they look."""

    def __init__(self, work):
        self.path = pathlib.Path(work) / 'gui.json'
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
        except OSError as e:
            print('q2emu: could not save settings:', e, file=sys.stderr, flush=True)


class EmulatorGUI:
    """The main application window: device shell + live firmware + controls.

    One window, freely resizable: the player is drawn to fit the space above the buttons. Everything
    else it shows (the More menu, questions, notes, help, tips) is drawn inside it, see controls.Overlay.
    Nothing slow runs on the Tk thread: files are saved, paths translated and Windows' dialogs asked on
    worker threads (_bg), whose results _refresh hands back."""

    BG, INK, DIM, GAP = controls.BG, controls.INK, controls.DIM, 6
    CONTROLS_MAX = 620  # the rows of buttons grow with the window up to this width

    def __init__(self, firmware_path=None, sd_card=None, strace=False, work=None):
        import tkinter as tk
        from PIL import ImageTk
        self.tk, self.ImageTk = tk, ImageTk

        self.runner = FirmwareRunner(work)
        self.settings = Settings(self.runner.work)
        self.strace = strace

        # Remembered from last time, unless given on the command line
        remembered_sd = self.settings.get('sd_card')
        self.runner.sd_card = sd_card or (pathlib.Path(remembered_sd) if remembered_sd
                                          and os.path.isdir(remembered_sd) else None)
        self.runner.internet = bool(self.settings.get('internet', False)) and bool(internet_tool())
        self.runner.measure = bool(self.settings.get('measure', False)) and can_count()
        remembered_fw = self.settings.get('firmware')
        self.last_firmware = (pathlib.Path(remembered_fw) if remembered_fw
                              and os.path.isfile(remembered_fw) else None)
        shell = self.settings.get('shell')
        self.shell = shell if shell in SHELL_FINISHES else DEFAULT_SHELL

        self._dragging = None
        self._last_angle = None
        self._clean = IDLE_SCREEN  # the screen as last drawn, without marks: what a screenshot saves
        self._shown = None         # what was last drawn, at the drawing's own size: a recording's frames
        self._shown_shell = None   # what the canvas last drew of the player...
        self._shown_screen = None  # ...and of its screen: identical redraws are skipped
        self._shell_photo = self._screen_photo = None
        self._shown_status = None  # what the status line and buttons last reported
        self._pixels_of = self._pixels = None  # the screen last drawn, and its bytes
        self._record_next = 0      # when a recording takes its next picture
        self._held = None          # (key, since): a wheel button the mouse is holding down
        self._booted = False       # the firmware has drawn something since it started
        self._notice = None        # (text, until): a passing message in the status line
        self._pointer = None       # where the mouse touches the device, in its image's coordinates
        self._pointer_down = False
        self._pointer_until = 0
        self._flashes = {}         # wheel button -> lit until
        self._touch_from = None    # a touch being recorded as a step: (x, y, time, moved)
        self.recorder = None
        self.door = None           # AI agent control, while it is enabled
        self._done = queue.Queue() # (then, result) from worker threads, for the Tk thread
        self._picking = False      # a file dialog is open
        self._captures = None      # the folder captures go to, once found
        self._quit = False         # asked to end (a signal): _refresh closes the window
        self._after = None
        self._steps = []
        self._step_at = 0
        self._turns, self._turn_queued, self._turn_lock = 0, 0, threading.Lock()  # wheel turns not yet made
        self._asleep = False       # the firmware has switched its screen off
        self._woke_at = 0
        self._settle_at = 0        # the window's size is still changing until then: quick drawing
        self._layout_width = None  # the width the buttons were last laid out for
        self._save_size_at = None

        # -- Window --
        self.window = tk.Tk(className='q2emulator')  # its class: what q2emulator.desktop names, for the taskbar icon
        self.window.title('Shanling Q2 Emulator')
        self.window.configure(bg=self.BG)
        self.window.minsize(340, 470)
        self.window.report_callback_exception = self._callback_error
        try:  # the window's own icon (the title bar, the taskbar), in the sizes they ask for
            icon = Image.open(HERE / 'icon.png')
            self._icons = [ImageTk.PhotoImage(icon.resize((s, s), Image.LANCZOS)) for s in (256, 64, 48, 32, 16)]
            self.window.iconphoto(True, *self._icons)
        except Exception as e:
            print('q2emu: no window icon:', e, file=sys.stderr, flush=True)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        # -- The player: fills whatever room the window has above the buttons --
        self.stage = tk.Frame(self.window, bg=self.BG)
        self.stage.grid(row=0, column=0, sticky='nsew', padx=10, pady=(10, 2))
        self.canvas = tk.Canvas(self.stage, highlightthickness=0, bg=self.BG, width=DEVICE_SIZE[0], height=DEVICE_SIZE[1])
        self.canvas.pack(fill='both', expand=True)
        self._shell_id = self.canvas.create_image(0, 0, anchor='nw')
        self._screen_id = self.canvas.create_image(0, 0, anchor='nw')
        self.view = DeviceView(self.BG)
        self.view_size, self.scale, self.origin = DEVICE_SIZE, 1.0, (0, 0)

        # -- Status: a dot for the player's state, and a line of plain words --
        line = tk.Frame(self.window, bg=self.BG)
        line.grid(row=1, column=0, sticky='ew', padx=12, pady=(2, 6))
        self._dot = tk.Canvas(line, width=10, height=10, highlightthickness=0, bg=self.BG)
        self._dot.pack(side='left', anchor='n', pady=4, padx=(2, 7))
        self._dot_id = self._dot.create_oval(1, 1, 9, 9, fill=controls.OFF, outline='')
        # two lines high whatever it says, so a longer message does not push the player about
        self.status = tk.Label(line, bg=self.BG, fg=self.DIM, font=('DejaVu Sans', 9), justify='left', anchor='nw', height=2)
        self.status.pack(side='left', fill='x')

        # -- Controls: the player's own on the first row, what goes into it on the second --
        # (the battery has none: q2emu keeps it at 100%)
        def button(parent, *args, **kw):
            return controls.Button(tk, ImageTk, parent, *args, **kw)
        # -- The player's memory, for those who ask for it (More): what its programs hold of a Q2's 128 MB --
        self.meter = tk.Label(self.window, bg=self.BG, fg=self.DIM, font=('DejaVu Sans', 8), justify='left', anchor='w')
        self._meter_shown = None
        self._work_mark = None     # the instructions counted at the last input
        self._over = False         # the firmware holds more memory than a Q2 has (said once each time)
        self.controls = tk.Frame(self.window, bg=self.BG)
        self.controls.grid(row=3, column=0, pady=(0, 12))
        row1 = tk.Frame(self.controls, bg=self.BG)
        row1.pack()
        self._power_btn = button(row1, 'tile', 'power', 'Power on', self._power)
        self._restart_btn = button(row1, 'tile', 'restart', 'Restart', self._restart,
                                   tip='Switch the player off and on again. Its settings are kept.')
        self._shot_btn = button(row1, 'tile', 'camera', 'Screenshot', self._screenshot, tip='Save a picture (F12)')
        self._rec_btn = button(row1, 'tile', 'record', 'Record', self._record, tip='Record a video (F9)')
        self._more_btn = button(row1, 'tile', 'more', 'More', self._more_popup,
                                tip='Shell color, start fresh, captures, AI agent control, help')
        self._tiles = (self._power_btn, self._restart_btn, self._shot_btn, self._rec_btn, self._more_btn)
        row2 = tk.Frame(self.controls, bg=self.BG)
        row2.pack(pady=(6, 0))
        self._fw_btn = button(row2, 'pill', 'folder', 'Open a firmware file', self._open_firmware,
                              heading='Firmware', tip='Open an update.tar or a firmware ZIP')
        self._sd_btn = button(row2, 'pill', 'card', 'None', self._mount_sd,
                              heading='SD card', tip='Choose a music folder to use as the SD card')
        self._pills = (self._fw_btn, self._sd_btn)
        for group in (self._tiles, self._pills):
            for i, b in enumerate(group):
                b.widget.pack(side='left', padx=(0, 0 if i == len(group) - 1 else self.GAP))
        self.overlay = controls.Overlay(tk, ImageTk, self.window, self.stage)
        self._shell_var = tk.StringVar(value=self.shell)
        self._device_var = tk.BooleanVar(value=self.settings.get('include_device', True))
        self._touches_var = tk.BooleanVar(value=self.settings.get('show_touches', True))
        self._steps_var = tk.BooleanVar(value=self.settings.get('save_steps', False))
        self._meter_var = tk.BooleanVar(value=self.settings.get('show_memory', False))
        self._agent_var = tk.BooleanVar(value=False)  # never remembered: off at every start
        self._sd_label()

        # -- Size: as it was left last time, else the player at its own size; never taller than the screen --
        width = DEVICE_SIZE[0] + 20
        self._layout(width)
        self.window.update_idletasks()
        below = line.winfo_reqheight() + 8 + self.controls.winfo_reqheight() + 12
        height = DEVICE_SIZE[1] + 12 + below
        saved = self.settings.get('window')
        if isinstance(saved, list) and len(saved) == 2 and all(isinstance(v, int) for v in saved):
            width, height = max(340, saved[0]), max(470, saved[1])
        width = min(width, self.window.winfo_screenwidth() - 40)
        height = min(height, self.window.winfo_screenheight() - 150)  # room for its title bar and the taskbar
        self._opened_size = (width, height)
        self.window.geometry(f'{width}x{height}')
        self._layout(width)

        # -- Input bindings --
        self.canvas.bind('<Configure>', self._on_canvas_size)
        self.window.bind('<Configure>', self._on_window_size)
        self.window.bind('<ButtonPress-1>', lambda e: self.overlay.outside(e.widget), add='+')
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<MouseWheel>', lambda e: self._wheel(-1 if e.delta > 0 else 1))
        self.canvas.bind('<Button-4>', lambda e: self._wheel(-1))
        self.canvas.bind('<Button-5>', lambda e: self._wheel(1))
        self.canvas.focus_set()
        for key, action in (
            ('<Return>', lambda: self._key('centre')),
            ('<Escape>', lambda: self._key('return')),
            ('<BackSpace>', lambda: self._key('return')),
            ('<space>', lambda: self._key('play')),
            ('<Left>', lambda: self._key('prev')),
            ('<Right>', lambda: self._key('next')),
            ('<Up>', lambda: self._wheel(-1)),
            ('<Down>', lambda: self._wheel(1)),
        ):  # with a panel up, a key puts it away instead of reaching the player
            self.canvas.bind(key, lambda e, a=action: self.overlay.close() if self.overlay.is_open else a())
        self.window.bind('<F9>', lambda e: self._record())
        self.window.bind('<F12>', lambda e: self._screenshot())

        self._bg(self._find_captures, self._captures_found)
        if firmware_path:
            self._load_firmware(pathlib.Path(firmware_path))
        elif self.last_firmware:  # as it was left: the last firmware starts by itself
            self._load_firmware(self.last_firmware)
        elif remembered_fw:
            self._say(f"The firmware used last time isn't where it was ({firmware_name(remembered_fw)}). "
                      'Click Firmware to open it again.', 30)
        self._refresh()

    # -- Worker threads --

    def _bg(self, work, then=None):
        """Run work() off the Tk thread; then(result) is called on it, from _refresh. A failure arrives
        as the exception itself."""
        def run():
            try:
                result = work()
            except Exception as e:
                traceback.print_exc()
                result = e
            self._done.put((then, result))
        threading.Thread(target=run, daemon=True).start()

    def _drain(self):
        while True:
            try:
                then, result = self._done.get_nowait()
            except queue.Empty:
                return
            if then:
                then(result)

    # -- Layout --

    def _layout(self, width):
        """The buttons and the status line for a window this wide."""
        w = max(300, min(width - 20, self.CONTROLS_MAX))
        if w == self._layout_width:
            return
        self._layout_width = w
        self.status.config(wraplength=max(200, width - 60))
        tile = (w - self.GAP * (len(self._tiles) - 1)) // len(self._tiles)
        for b in self._tiles:
            b.set(size=(tile, 60))
        for b in self._pills:
            b.set(size=((w - self.GAP) // 2, 50))

    def _on_canvas_size(self, event):
        size, scale = DeviceView.fit(event.width, event.height)
        origin = ((event.width - size[0]) // 2, (event.height - size[1]) // 2)
        if (size, origin) != (self.view_size, self.origin):
            if size != self.view_size:
                self._settle_at = time.time() + 0.25  # drawn quickly while it changes, with care once it rests
            self.view_size, self.scale, self.origin = size, scale, origin

    def _on_window_size(self, event):
        if event.widget is self.window:
            self._save_size_at = time.time() + 0.8

    def _settled(self):
        """The window's size has come to rest: lay the buttons out for it, and remember it."""
        width, height = self.window.winfo_width(), self.window.winfo_height()
        if width <= 1:
            return  # not on screen yet
        self._layout(width)
        if self._save_size_at and time.time() >= self._save_size_at:
            self._save_size_at = None
            try:
                zoomed = bool(self.window.attributes('-zoomed'))
            except Exception:
                zoomed = False
            if not zoomed and (width, height) != self._opened_size and width > 1:
                self._opened_size = (width, height)
                self.settings.set('window', [width, height])

    # -- Panels inside the window --

    def _more_popup(self, again=False):
        o = self.overlay
        if not again and o.closed_kind == 'menu' and time.time() - o.closed_at < 0.3:
            return  # this click on More is the one that just closed the menu
        o.open('menu', 'bottom')
        o.row('Start this firmware fresh...', self._start_fresh, enabled=bool(self._firmware_to_start()))
        o.row('Eject SD card', self._eject_sd, enabled=bool(self.runner.sd_card))
        o.line()
        o.choices('Shell color', list(SHELL_FINISHES), self.shell,
                  {name: finish.top for name, finish in SHELL_FINISHES.items()}, self._pick_shell)
        o.line()

        def flip(var, key):
            var.set(not var.get())
            self.settings.set(key, var.get())
            self._more_popup(again=True)
        o.row('Show the player in screenshots and videos', lambda: flip(self._device_var, 'include_device'),
              checked=self._device_var.get(), keep_open=True)
        o.row('Show touches and button presses', lambda: flip(self._touches_var, 'show_touches'),
              checked=self._touches_var.get(), keep_open=True)
        o.row('Save my clicks with a video, to replay it', lambda: flip(self._steps_var, 'save_steps'),
              checked=self._steps_var.get(), keep_open=True)
        o.row("Show the player's memory use", lambda: flip(self._meter_var, 'show_memory'),
              checked=self._meter_var.get(), keep_open=True)
        o.row('Open the captures folder', self._open_captures)
        o.row('Change the captures folder...', self._choose_captures)
        o.line()

        def agent():
            self._agent_var.set(not self._agent_var.get())
            self._toggle_agent()
        o.row('AI agent control', agent, checked=self._agent_var.get())
        o.row('Internet for the player (pretend Wi-Fi)', self._toggle_internet, checked=self.runner.internet)
        if can_count():
            o.row("Measure the player's work (a little slower)", self._toggle_measure, checked=self.runner.measure)
        o.row('Help and keyboard shortcuts', self._help)
        if os.environ.get('Q2EXE'):  # started by the Windows launcher, which is also what removes it
            o.row('Remove Q2 Emulator from this PC...', self._uninstall)

    def _ask(self, title, text, yes, on_yes, no='Cancel'):
        """A question, over the player. yes calls on_yes(); no, or a click anywhere else, just closes it."""
        o = self.overlay
        o.open('ask', 'center')
        o.title(title)
        o.text(text)
        o.buttons((no, None, False), (yes, on_yes, True))

    def _note(self, title, text, action=None, seconds=8):
        """A note at the top of the player that goes away by itself. action: (button text, command)."""
        o = self.overlay
        if o.is_open and o.kind != 'note':
            return  # a menu or a question is up: the status line says it too
        o.open('note', 'top', seconds=seconds)
        o.title(title)
        o.text(text, dim=True, small=True)
        if action:
            o.buttons((action[0], action[1], False))

    def _uninstall(self):
        """Hand over to the Windows launcher, which asks before it removes anything."""
        try:
            subprocess.Popen([os.environ['Q2EXE'], '--uninstall'], cwd='/mnt/c',
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self._say(f"The uninstaller couldn't be started: {e}")

    def _help(self):
        o = self.overlay
        o.open('help', 'center')
        for heading, rows in HELP:
            o.heading(heading)
            o.pairs(rows)
        o.heading('SD card')
        o.text(SD_HELP, dim=True, small=True)
        o.buttons(('Close', None, True))

    _shortcuts = _help

    # -- Rendering --

    def _screen(self):
        r = self.runner
        if r.state == 'loading':
            return _message_screen([('Loading firmware...', '#c0c0c0', 16), (r.message, '#777777', 12)])
        self._asleep = False
        if r.state == 'running':
            frame = r.frame()
            if frame is None:
                return _message_screen([('Starting...', '#777777', 13)])
            if not self._booted and r.started() and frame.getbbox() is not None:
                self._booted = True  # the firmware's own first picture (before it: the loading screen)
            # The firmware's idle timer switches the panel off and leaves the last picture in memory. Shown
            # as that picture, the player looks frozen: it ignores touches until its power key wakes it.
            self._asleep = self._booted and r.screen_off()
            return SLEEP_SCREEN if self._asleep else frame
        if r.state == 'off':
            return OFF_SCREEN
        if r.state in ('stopped', 'error', 'busy'):
            return _message_screen([('Stopped' if r.state == 'stopped' else 'Not started', '#ff6666', 14),
                                    (r.message, '#cc9999', 13)], bg='#1a0505')
        return IDLE_SCREEN

    def _marks(self):
        if not self._touches_var.get():
            return ()
        now, marks = time.time(), []
        if self._pointer and (self._pointer_down or now < self._pointer_until):
            marks.append(('touch', round(self._pointer[0]), round(self._pointer[1]), 13))
        for name, until in list(self._flashes.items()):
            if now < until:
                marks.append(('button', *BUTTON_AT[name]))
            else:
                del self._flashes[name]
        return tuple(marks)

    FRAME = 16  # ms between looks at a running player's screen: the firmware draws 60 pictures a second

    def _refresh(self):
        delay, began = 200, time.perf_counter()
        try:
            if self._quit:
                self._quit = False
                return self._close()
            self._drain()
            r = self.runner
            r.poll()
            screen = self._clean = self._screen()
            marks = self._marks()
            self._update_status()
            self._update_meter()
            fine = time.time() >= self._settle_at
            if fine:
                self._settled()
            if screen is not self._pixels_of:  # an unchanged screen is the same picture (Q2.frame): no work at all
                self._pixels_of, self._pixels = screen, screen.tobytes()
            pixels = self._pixels
            on_screen = tuple(m for m in marks if self._screen_coords(m[1], m[2]))
            # The player: only when its size, colour or a lit button changes
            key = (self.view_size, self.origin, self.shell, tuple(m for m in marks if m not in on_screen), fine)
            if key != self._shown_shell:
                self._shown_shell = key
                self._shell_photo = self.ImageTk.PhotoImage(self.view.shell(self.view_size, self.shell, key[3], fine))
                self.canvas.itemconfigure(self._shell_id, image=self._shell_photo)
                self.canvas.coords(self._shell_id, *self.origin)
            # Its screen: with every new frame
            key = (r.state, self.view_size, self.origin, self.shell, fine, on_screen, pixels)
            if key != self._shown_screen:
                self._shown_screen = key
                picture, (x, y) = self.view.screen(self.view_size, self.shell, screen, on_screen)
                self._screen_photo = self.ImageTk.PhotoImage(picture)
                self.canvas.itemconfigure(self._screen_id, image=self._screen_photo)
                self.canvas.coords(self._screen_id, self.origin[0] + x, self.origin[1] + y)
            # A recording: its own picture, at the drawing's size, when anything in it changed
            # (no more often than the video has frames: a picture skipped here is still unlike _shown next time)
            key = (r.state, self.shell, marks, pixels)
            if key != self._shown and (not self.recorder or began >= self._record_next):
                self._shown = key
                self._record_next = began + 1 / Recorder.FPS
                self._record_frame(screen, marks)
            if self.recorder:
                self.recorder.tick()  # an unchanged picture still takes up the video's time
            if r.state == 'busy':
                self._offer_take_over()
            if r.state == 'running':  # as often as the firmware draws, less the time this took
                delay = max(4, self.FRAME - int((time.perf_counter() - began) * 1000))
            elif marks or self.recorder or not fine:
                delay = 50
        except Exception:  # one bad frame must not end the window's refresh for good
            traceback.print_exc()
        self._after = self.window.after(delay, self._refresh)

    def _callback_error(self, kind, value, trace):
        traceback.print_exception(kind, value, trace)
        self._say(f'Something went wrong: {value}')

    def _say(self, text, seconds=7):
        """A passing message in the status line."""
        self._notice = (text, time.time() + seconds)

    def _update_status(self):
        r = self.runner
        if r.state != 'running':
            self._booted = False
        if self._notice and time.time() > self._notice[1]:
            self._notice = None
        recording = int(self.recorder.seconds) if self.recorder else None
        now = (r.state, r.message, r.hint, self._booted, self._notice, recording, self._asleep)
        if now == self._shown_status:
            return
        self._shown_status = now
        name = firmware_name(r.firmware) if r.firmware else None
        if self._notice:
            text = self._notice[0]
        elif r.state == 'idle':
            text = (f'Last firmware: {firmware_name(self.last_firmware)}. Press Power on to start it, '
                    'or Open firmware to pick another.' if self.last_firmware
                    else 'Click Open firmware to load an update.tar or a firmware ZIP.')
        elif r.state == 'loading':
            text = r.message
        elif r.state == 'running':
            text = ('The player switched its screen off after a while without input, as a real one does. '
                    'Click it or press any key to wake it.' if self._asleep
                    else f'Running {name}. Touch the screen, use the wheel or the keyboard.' if self._booted
                    else f'Booting {name}... (about 10 seconds)')
        elif r.state == 'off':
            text = r.message + ' Press Power on to start it again.'
        else:
            text = r.message + (' ' + r.hint if r.hint else '')
        self.status.config(text=text, height=4 if r.state in ('error', 'stopped') and not self._notice else 2)
        self._dot.itemconfigure(self._dot_id, fill={
            'running': controls.GREEN if self._booted else '#e0b040', 'loading': '#e0b040',
            'stopped': '#e0584e', 'error': '#e0584e', 'busy': '#e0584e'}.get(r.state, controls.OFF))
        # Buttons follow the state
        on = r.state in ('running', 'loading')
        self._power_btn.set(text='Power off' if on else 'Power on', tone='on' if on else '',
                            enabled=bool(on or self._firmware_to_start()),
                            tip='Switch the player off' if on else 'Start the last firmware' if self._firmware_to_start()
                            else 'Open a firmware first')
        self._restart_btn.set(enabled=r.state == 'running')
        if recording is None:
            self._rec_btn.set(icon='record', text='Record', tone='', tip='Record a video (F9)')
        else:
            self._rec_btn.set(icon='stop', text=f'Stop {recording // 60}:{recording % 60:02d}', tone='rec',
                              tip='Stop and save the video (F9)')
        shown = self._firmware_to_start()
        self._fw_btn.set(text=firmware_name(shown) if shown else 'Open a firmware file')
        self.window.title('Shanling Q2 Emulator' + (f' - {name}' if name else ''))
        if r.state == 'running' and not self.overlay.is_open:
            self.canvas.focus_set()

    MEMORY_FULL = 111  # MB: where a Q2 has nothing left for its programs (q2emu.meminfo: 117864 kB less 4 MB)

    def _update_meter(self):
        """The line under the status: what the firmware's programs hold of a Q2's memory."""
        self._memory_warning()
        used = self.runner.memory() if self._meter_var.get() else None
        peak = self.runner.memory_peak()
        work = self.runner.instructions()
        if work is not None and self._work_mark is None:
            self._work_mark = work
        lines, over = [], False
        if used is not None:
            total, top = round(used[0]), ', '.join(f'{name} {mb:.0f}' for name, mb in used[1][:3] if mb >= 1)
            over = total > self.MEMORY_FULL
            lines.append(f"Memory: {total} MB of the Q2's {Q2_RAM_MB} MB"
                         + (' - more than a real Q2 has free: there, this would fail' if over else '')
                         + (f', at most {round(peak[0])}' if peak and round(peak[0]) > total else '')
                         + (f'   ({top})' if top else ''))
        if work is not None:
            since = max(0, work - self._work_mark)
            lines.append(f'Work: {_count_words(since)} instructions since your last input '
                         f"(at least {since / Q2_INSNS_PER_S:.1f} s of a Q2's processor), {_count_words(work)} since it started")
        now = (tuple(lines), over)
        if now == self._meter_shown:
            return
        self._meter_shown = now
        if not lines:
            return self.meter.grid_remove()
        self.meter.config(fg='#e0584e' if over else self.DIM, text='\n'.join(lines))
        self.meter.grid(row=2, column=0, sticky='ew', padx=31, pady=(0, 6))

    def _memory_warning(self):
        """Nothing is stopped when the firmware takes more memory than a Q2 has: it is said, once each time
        it goes over, whether the meter is shown or not. That is what the meter is for: what runs here
        and would not on the player."""
        used = self.runner.memory()
        if used is None:
            self._over = False
            return
        if used[0] > self.MEMORY_FULL and not self._over:
            self._over = True
            name, mb = used[1][0]
            text = (f'The firmware is using {round(used[0])} MB. A real Q2 has 128 MB in all, about {self.MEMORY_FULL} MB of it '
                    f'for its programs. The largest is {name}, with {round(mb)} MB. The emulator lets it run on; '
                    'a real Q2 would likely stop it or fail here.')
            self._say(f'More memory than a Q2 has: {round(used[0])} MB in use ({name} {round(mb)} MB).', 15)
            self._note('More memory than a real Q2 has', text, seconds=14)
        elif used[0] < self.MEMORY_FULL * 0.9:
            self._over = False

    def _mark_work(self):
        """An input: the work counted from here on is what it cost."""
        self._work_mark = self.runner.instructions()

    def _firmware_to_start(self):
        return self.runner.firmware or self.last_firmware

    # -- Screenshots and recordings --

    def _find_captures(self):
        """(Worker thread, or the first capture if that comes sooner.) The folder captures go to, made
        if need be: the one chosen, else Windows' Videos, else the home folder. None if none can be."""
        saved = self.settings.get('captures')
        folder = pathlib.Path(saved) if saved else default_captures_dir()
        for choice in (folder, pathlib.Path.home() / 'Q2 Emulator'):
            try:
                choice.mkdir(parents=True, exist_ok=True)
                _hand_over(choice)
                return choice
            except OSError:
                continue
        return None

    def _captures_found(self, folder):
        if isinstance(folder, pathlib.Path) and not self._captures:
            self._captures = folder
            if self.settings.get('captures') != str(folder):
                self.settings.set('captures', str(folder))

    def _captures_dir(self):
        """The folder captures go to; None (and a message) if there is none."""
        if not self._captures:
            self._captures_found(self._find_captures())
        if self._captures:
            try:
                self._captures.mkdir(parents=True, exist_ok=True)
                return self._captures
            except OSError:
                self._captures = None
        self._say("The captures folder couldn't be created. Pick one with More > Change the captures folder.")
        return None

    def _capture_path(self, ending):
        folder = self._captures_dir()
        return folder / time.strftime(f'q2-%Y%m%d-%H%M%S{ending}') if folder else None

    def _saved(self, what, path, shown):
        """A capture is on disk (or failed): say so where it cannot be missed, with its full path."""
        if isinstance(shown, Exception):
            return self._say(f"The {what.lower()} couldn't be saved: {shown}", 12)
        self._say(f'{what} saved: {shown}', 8)
        self._note(f'{what} saved', shown, ('Show in folder', lambda: self._reveal(path)))

    def _screenshot(self):
        path = self._capture_path('.png')
        if not path:
            return
        # With the player: nothing around it (transparent), so the picture sits on any background
        image = player(self._clean, self.shell, backdrop=None) if self._device_var.get() else self._clean.copy()

        def save():
            image.save(path)
            _hand_over(path)
            return shown_path(path)
        self._bg(save, lambda shown: self._saved('Screenshot', path, shown))

    def _record(self):
        if self.recorder:
            return self._stop_recording()
        if not shutil.which('ffmpeg'):
            return self._say('Recording needs ffmpeg, which is not installed.')
        path = self._capture_path('.mp4')
        if not path:
            return
        self._rec_device = self._device_var.get()  # fixed for this recording: the picture's size
        try:
            self.recorder = Recorder(path, DEVICE_SIZE if self._rec_device else SCREEN_SIZE)
        except OSError as e:
            return self._say(f"Recording couldn't start: {e}")
        self._steps, self._step_at = [], time.time()
        self._shown = None  # draw now, so the video starts with the current picture

    def _record_frame(self, screen, marks):
        if not self.recorder:
            return
        if self._rec_device:
            frame = draw_marks(player(screen, self.shell), marks)
        else:
            frame = draw_marks(screen, marks, SCREEN_AT)
        self.recorder.write(frame)
        if self.recorder.failed:
            self._stop_recording()

    def _finish_recording(self, recorder, steps):
        """(Worker thread.) Close the video file; the path to show, or an error raised."""
        error = recorder.stop()
        if error:
            raise RuntimeError(error[:200])
        if steps:  # what was pressed, as a q2emu --script: replays the recording on any emulator
            file = recorder.path.with_suffix('.steps.txt')
            try:
                file.write_text(';\n'.join(steps) + '\n')
                _hand_over(file)
            except OSError:
                pass
        return shown_path(recorder.path)

    def _stop_recording(self):
        recorder, self.recorder = self.recorder, None
        steps, self._steps = (self._steps if self._steps_var.get() else []), []
        self._shown_status = None
        self._say('Saving the video...', 60)
        self._bg(lambda: self._finish_recording(recorder, steps),
                 lambda shown: self._saved('Video', recorder.path, shown))

    def _step(self, text):
        """Note an input for the recording's steps file, with the pause before it."""
        if not self.recorder or self.runner.state != 'running':
            return
        now = time.time()
        pause = now - self._step_at
        if pause >= 0.1:
            self._steps.append(f'wait {pause:.1f}')
        self._steps.append(text)
        self._step_at = now

    def _reveal(self, path):
        """Open the file's folder in Windows' Explorer with the file selected."""
        self._bg(lambda: reveal_in_explorer(path),
                 lambda r: self._say(f'It is in: {path.parent}', 10) if r is not True else None)

    def _open_captures(self):
        folder = self._captures_dir()
        if not folder:
            return

        def go():
            shown = shown_path(folder)
            subprocess.Popen([EXPLORER, shown], cwd='/mnt/c', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return shown
        self._bg(go, lambda r: self._say(f'Captures are saved in: {folder}' if isinstance(r, Exception)
                                         else f'Captures folder: {r}', 10))

    def _pick(self, kind, title, start, then):
        """Ask for a file or a folder, then call then(path) on the Tk thread. Windows' own dialog under
        WSL (asked from a thread, so the player keeps running behind it), Tk's otherwise."""
        if self._picking:
            return self._say('A file window is already open. Look for it behind this one.', 4)
        self._picking = True
        self._bg(lambda: windows_dialog(kind, title, start),
                 lambda path: self._pick_done(path, kind, title, start, then))

    def _pick_done(self, path, kind, title, start, then):
        self._picking = False
        if path is None or isinstance(path, Exception):  # no Windows dialogs here: Tk's
            from tkinter import filedialog
            if kind == 'file':
                path = filedialog.askopenfilename(title=title, initialdir=start or None,
                                                  filetypes=[('Firmware', '*.tar *.zip'), ('All files', '*.*')])
            else:
                path = filedialog.askdirectory(title=title, initialdir=start or None)
        if path:
            then(path)

    def _choose_captures(self):
        def chosen(path):
            self._captures = pathlib.Path(path)
            self.settings.set('captures', path)
            self._bg(lambda: shown_path(path), lambda shown: self._say(f'Captures will be saved in: {shown}'))
        self._pick('folder', 'Where should screenshots and videos be saved?', self.settings.get('captures'), chosen)

    # -- Hit testing --

    def _device_xy(self, event):
        """The mouse's place in the device image, whatever the window's size."""
        return (event.x - self.origin[0]) / self.scale, (event.y - self.origin[1]) / self.scale

    def _screen_coords(self, x, y):
        sx, sy = x - SCREEN_AT[0], y - SCREEN_AT[1]
        if 0 <= sx < SCREEN_SIZE[0] and 0 <= sy < SCREEN_SIZE[1]:
            return int(sx), int(sy)
        return None

    # -- Input events (all queued, in order, to the runner's input thread) --

    def _wake(self):
        """With the player's screen off, any input only wakes it, as on the real one: True if it was off.
        The wheel's center button is the Q2's power key, and that is what wakes it."""
        if not self._asleep:
            return False
        if time.time() - self._woke_at > 1.0:  # once, however many clicks arrive before it is awake
            self._woke_at = time.time()
            self._flashes['center'] = time.time() + 0.18
            self._step('key centre')
            self.runner.send(lambda q2: q2.key('centre'))
        return True

    def _key(self, name):
        if self._wake():
            return
        self._mark_work()
        with self._turn_lock:
            self._turns = 0  # a button overrides wheel turns still waiting, as it does on the player
        self._flashes[KEY_BUTTONS[name]] = time.time() + 0.18
        self._step(f'key {name}')
        self.runner.send(lambda q2: q2.key(name, self.TAP))

    TAP = 125  # ms a key of the keyboard holds its button: what a press lasts on a real Q2 (the middle of 36 timed)

    def _hold(self, name):
        """The mouse pressed one of the wheel's buttons: it goes down now and comes up with the mouse, so it
        answers as fast as the click is, and a long press is a long press."""
        self._mark_work()
        with self._turn_lock:
            self._turns = 0
        self._held = (name, time.time())
        self._flashes[KEY_BUTTONS[name]] = float('inf')  # lit for as long as it is held
        self.runner.send(lambda q2: q2.press(name))

    def _let_go(self):
        if not self._held:
            return
        (name, since), self._held = self._held, None
        held = time.time() - since
        self._flashes[KEY_BUTTONS[name]] = time.time() + 0.12
        self._step(f'key {name}' if held < 0.3 else f'key {name} {round(held * 1000)}')

        def up(q2):
            time.sleep(max(0, self.TAP / 1000 - (time.time() - since)))  # never shorter than a key's tap
            q2.press(name, False)
        self.runner.send(up)

    def _wheel(self, ticks):
        """A turn of the wheel. The firmware takes one as a finger's stroke lasting a sixth of a second
        or more, so turns that arrive while one is still being made (a spin of the mouse wheel) are
        added up and made as one stroke: the player keeps up instead of working through a queue."""
        if self._wake() or not self.runner.running:
            return
        self._step(f'wheel {ticks}')
        self._mark_work()
        with self._turn_lock:
            self._turns += ticks
            waiting = time.time() - self._turn_queued < 3
            if not waiting:
                self._turn_queued = time.time()
        if not waiting:
            self.runner.send(self._turn)

    TURN = 6  # the most turns made as one stroke (a third of a second): a button pressed meanwhile is not kept waiting

    def _turn(self, q2):
        """(Input thread.) Make the turns added up so far, a short stroke at a time."""
        with self._turn_lock:
            ticks = max(-self.TURN, min(self.TURN, self._turns))
            self._turns -= ticks
            more = self._turns != 0
            self._turn_queued = time.time() if more else 0
        if ticks:
            q2.wheel(ticks)
        if more:
            self.runner.send(self._turn)  # behind any button pressed since

    def _on_press(self, event):
        if self.overlay.outside(getattr(event, 'widget', None)):
            return  # the click put a menu or a question away: it is not also a touch
        if self._wake():
            return
        self.canvas.focus_set()  # keys go to the player again after a button was clicked
        x, y = self._device_xy(event)
        sc = self._screen_coords(x, y)
        if sc:
            self._pointer, self._pointer_down = (x, y), True
            self._touch_from = (sc[0], sc[1], time.time(), False)
            self._mark_work()
            self.runner.send(lambda q2: q2.touch(sc[0], sc[1], first=True))
            self._dragging = 'screen'
            return
        btn = wheel_button(x, y)
        if btn:
            if btn in WHEEL_KEYS:
                self._hold(WHEEL_KEYS[btn])
            self._last_angle = wheel_angle(x, y)
            self._dragging = 'wheel'
        elif self._on_bezel(x, y):
            # A finger that starts on the frame and slides onto the glass: the touch begins at the screen's
            # very edge, which is where the firmware wants a swipe to start (its panel from the top edge).
            self._dragging = 'edge'

    EDGE = 34  # how far around the screen a press still counts as on its frame, in the drawing's pixels

    def _on_bezel(self, x, y):
        sx, sy = x - SCREEN_AT[0], y - SCREEN_AT[1]
        return -self.EDGE <= sx < SCREEN_SIZE[0] + self.EDGE and -self.EDGE <= sy < SCREEN_SIZE[1] + self.EDGE

    def _on_drag(self, event):
        x, y = self._device_xy(event)
        if self._dragging == 'edge':
            sc = self._screen_coords(x, y)
            if sc:  # it has reached the glass: the touch starts here, on the edge it came in over
                sx, sy = x - SCREEN_AT[0], y - SCREEN_AT[1]
                near = min((sy, 'top'), (SCREEN_SIZE[1] - 1 - sy, 'bottom'), (sx, 'left'), (SCREEN_SIZE[0] - 1 - sx, 'right'))[1]
                at = {'top': (sc[0], 0), 'bottom': (sc[0], SCREEN_SIZE[1] - 1),
                      'left': (0, sc[1]), 'right': (SCREEN_SIZE[0] - 1, sc[1])}[near]
                self._pointer, self._pointer_down = (x, y), True
                self._touch_from, self._touch_to = (at[0], at[1], time.time(), True), sc
                self.runner.send(lambda q2: q2.touch(at[0], at[1], first=True))
                self.runner.send(lambda q2: q2.touch(sc[0], sc[1]))
                self._dragging = 'screen'
        elif self._dragging == 'screen':
            sc = self._screen_coords(x, y)
            if sc:
                self._pointer = (x, y)
                if self._touch_from:
                    x0, y0, t0, moved = self._touch_from
                    self._touch_from = (x0, y0, t0, moved or abs(sc[0] - x0) + abs(sc[1] - y0) > 8)
                    self._touch_to = sc
                self.runner.send(lambda q2: q2.touch(sc[0], sc[1]))
        elif self._dragging == 'wheel' and self._last_angle is not None:
            angle = wheel_angle(x, y)
            ticks = int(wheel_delta(self._last_angle, angle) / DETENT)
            if ticks:
                self._let_go()  # the finger slides on: a turn of the wheel, no longer a press
                self._pointer, self._pointer_down = (x, y), True
                self._wheel(ticks)
                self._last_angle = angle

    def _on_release(self, event):
        if self._dragging == 'screen':
            self.runner.send(lambda q2: q2.touch(0, 0, down=False))
            if self._touch_from:
                x0, y0, t0, moved = self._touch_from
                if moved:
                    x1, y1 = self._touch_to
                    self._step(f'swipe {x0} {y0} {x1} {y1} {max(100, round((time.time() - t0) * 1000))}')
                else:
                    self._step(f'tap {x0} {y0}')
        self._let_go()
        self._touch_from = None
        self._pointer_down, self._pointer_until = False, time.time() + 0.15
        self._dragging = None
        self._last_angle = None

    # -- Controls --

    def _busy_note(self):
        self._say('Still loading; wait a moment for it to finish.', 4)

    def _load_firmware(self, firmware_path, fresh=False, take_over=False):
        if not self.runner.load(firmware_path, strace=self.strace, fresh=fresh, take_over=take_over):
            self._busy_note()
            return
        self._notice = None
        self.last_firmware = pathlib.Path(firmware_path)
        self.settings.set('firmware', str(self.last_firmware))

    def _open_firmware(self):
        if self.runner.loading:
            return self._busy_note()
        start = self._firmware_to_start()
        self._pick('file', 'Open Q2 firmware (update.tar or a firmware ZIP)', str(start.parent) if start else None,
                   lambda path: self._load_firmware(pathlib.Path(path)))

    def _power(self):
        r = self.runner
        if r.state in ('running', 'loading'):
            if not r.power_off():
                self._busy_note()
        elif self._firmware_to_start():
            self._load_firmware(self._firmware_to_start())

    def _restart(self):
        """Like switching the player off and on: the firmware starts again, keeping its saved settings."""
        if self.runner.state == 'running':
            self._load_firmware(self.runner.firmware)

    def _start_fresh(self):
        fw = self._firmware_to_start()
        if not fw:
            return self._say('Open a firmware first.')
        if self.runner.loading:
            return self._busy_note()
        self._ask('Start this firmware fresh?',
                  f'{firmware_name(fw)} will start like the very first time: the settings it saved '
                  '(language, theme and the rest) are erased.\n\nYour firmware file and music are not touched.',
                  'Start fresh', lambda: self._load_firmware(fw, fresh=True))

    def _offer_take_over(self):
        r = self.runner
        r.state = 'error'  # asked once; answering No leaves the explanation on screen
        r.message, r.hint = ('Another emulator window is already running.',
                             'Close it, then press Power on.')
        self._ask('Another emulator is running',
                  'Another Q2 Emulator is already running, maybe in a window you closed or one that is hidden.',
                  'Close it and start here', lambda: self._load_firmware(self._firmware_to_start(), take_over=True))

    def _sd_label(self):
        sd = self.runner.sd_card
        self._sd_btn.set(text=sd.name if sd else 'None', icon='card',
                         tip=f'{sd}\nClick to choose another folder' if sd
                         else 'Choose a music folder to use as the SD card')

    def _sd_changed(self):
        self._sd_label()
        sd = self.runner.sd_card
        self.settings.set('sd_card', str(sd) if sd else None)
        if self.runner.state == 'running':
            self._ask('SD card changed' if sd else 'SD card ejected',
                      'The player looks for its card when it starts.' + (' Then open Folder on the player to see your music.' if sd else ''),
                      'Restart the player', self._restart, 'Later')
        elif sd:
            self._say(f'SD card: {sd.name}. The player will find it when it starts.')

    def _mount_sd(self):
        def chosen(path):
            self.runner.sd_card = pathlib.Path(path)
            self._sd_changed()
        sd = self.runner.sd_card
        self._pick('folder', 'Choose a music folder to use as the SD card', str(sd) if sd else None, chosen)

    def _eject_sd(self):
        if self.runner.sd_card:
            self.runner.sd_card = None
            self._sd_changed()

    def _pick_shell(self, name):
        self._shell_var.set(name)
        self._set_shell()

    def _set_shell(self):
        self.shell = self._shell_var.get()
        self.settings.set('shell', self.shell)

    def _toggle_agent(self):
        """Turn AI agent control on or off (agent_door.py): this computer only, with a token."""
        if self._agent_var.get() and not self.door:
            try:
                self.door = agent_door.Door(self.runner, on_input=self._agent_input,
                                            player=lambda screen: player(screen, self.shell, backdrop=None))
                self._say(f'AI agent control enabled (this computer only, port {self.door.port}). '
                          'Turn it off in More.', 12)
            except OSError as e:
                self._agent_var.set(False)
                self._say(f"AI agent control couldn't be enabled: {e}")
        elif not self._agent_var.get() and self.door:
            self.door.close()
            self.door = None
            self._say('AI agent control disabled.', 5)

    def _toggle_internet(self):
        """The player's internet, on or off (wifi.py): it takes effect when the player next starts."""
        if self.runner.internet:
            return self._internet_set(False)
        if internet_tool():
            return self._internet_set(True)
        self._say('Setting up the internet for the player (once, a few seconds)...', 60)
        self._bg(get_internet_tool, lambda ok: self._internet_set(True) if ok is True else self._say(
            "The internet couldn't be set up: the emulator's Linux needs the slirp4netns package, "
            'and this PC needs to be online to fetch it.', 14))

    def _toggle_measure(self):
        """Measuring mode on or off: the firmware's instructions are counted, from the player's next start."""
        on = self.runner.measure = not self.runner.measure
        self.settings.set('measure', on)
        self._work_mark = None
        text = ('The emulator will count the instructions the firmware executes: how much work a thing takes, '
                'which is the same on any PC and on a real Q2, where the time it takes here is not. '
                'It shows under the status line. The player runs about a sixth slower meanwhile.'
                if on else 'The firmware will run at its usual speed, uncounted.')
        if self.runner.state == 'running':
            self._ask('Measuring on' if on else 'Measuring off', text + '\n\nIt takes effect when the player restarts.',
                      'Restart the player', self._restart, 'Later')
        else:
            self._say("Measuring the player's work: " + ('on.' if on else 'off.'), 8)

    def _internet_set(self, on):
        self.runner.internet = on
        self.settings.set('internet', on)
        if self.runner.state == 'running':
            self._ask('Internet on' if on else 'Internet off',
                      ('The player will find one Wi-Fi network, "Emulator Wi-Fi", with this PC\'s internet behind it. '
                       'Turn Wi-Fi on in the player and pick it; no password. What the player downloads is real.'
                       if on else 'The player will have no network at all, as before.')
                      + '\n\nIt takes effect when the player restarts.',
                      'Restart the player', self._restart, 'Later')
        else:
            self._say('Internet for the player: on. In the player, turn Wi-Fi on and pick "Emulator Wi-Fi".' if on
                      else 'Internet for the player: off.', 10)

    def _agent_input(self, kind, data):
        """From the agent's thread: only sets what _refresh draws (where the agent touched, what it pressed)."""
        if kind == 'touch':
            self._pointer = (data[0] + SCREEN_AT[0], data[1] + SCREEN_AT[1])
            self._pointer_down, self._pointer_until = False, time.time() + 0.45
        elif kind == 'key':
            self._flashes[KEY_BUTTONS[data]] = time.time() + 0.3

    # -- Lifecycle --

    def _close(self):
        """Stop everything and close the window."""
        if self.recorder:
            recorder, self.recorder = self.recorder, None
            self._finish_recording_quietly(recorder)
        if self.door:
            self.door.close()
            self.door = None
        # A load still under way would start the firmware after the window is gone, as an orphan:
        # mark closing, then wait for the loader to notice and stop what it started.
        self.runner.close()
        if self.runner.loading:
            self.status.config(text='Closing once the firmware load finishes...')
            self.window.after(200, self._close)
            return
        if self._after:
            self.window.after_cancel(self._after)
        self.window.destroy()

    def _finish_recording_quietly(self, recorder):
        try:
            self._finish_recording(recorder, self._steps if self._steps_var.get() else [])
        except Exception as e:
            print('q2emu: the recording could not be saved:', e, file=sys.stderr, flush=True)

    def run(self):
        self.window.protocol('WM_DELETE_WINDOW', self._close)
        # Told to end from outside (the launcher closing, a terminal going away): close properly, so the
        # firmware is stopped and its mounts undone. _refresh notices within a fifth of a second.
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, lambda *_: setattr(self, '_quit', True))
        self.window.mainloop()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--firmware', type=pathlib.Path,
                    help='an update.tar, or a stock ZIP holding one')
    ap.add_argument('--work', type=pathlib.Path,
                    default=pathlib.Path.home() / '.cache/q2emu',
                    help='where the rootfs is unpacked (default: %(default)s)')
    ap.add_argument('--sd-card', type=pathlib.Path,
                    help='folder to mount as the SD card (/mnt/mmc)')
    ap.add_argument('--strace', action='store_true',
                    help='log system calls to demo.log')
    a = ap.parse_args()
    if os.geteuid():
        sys.exit('q2emu needs root: chroot, mounts and binfmt_misc.\n'
                 '  Run with: ./start.sh')
    gui = EmulatorGUI(
        firmware_path=a.firmware,
        sd_card=a.sd_card,
        strace=a.strace,
        work=a.work,
    )
    gui.run()


if __name__ == '__main__':
    main()
