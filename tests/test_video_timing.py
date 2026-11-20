"""Where does each colour change land in the recorded video, against when it was written?"""
import pathlib, subprocess, sys, tempfile, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import emulator_gui as g
from PIL import Image
tmp = pathlib.Path(tempfile.mkdtemp())
for warm in (0.0, 1.0):
    rec = g.Recorder(tmp/f'v{warm}.mp4', g.SCREEN_SIZE)
    time.sleep(warm)
    t0 = time.time(); wrote = []
    for i, pause in enumerate((0.2, 1.3, 0.1, 1.4)):
        wrote.append(round(time.time() - t0, 2)); rec.write(Image.new('RGB', g.SCREEN_SIZE, (60 * i + 20, 0, 0))); time.sleep(pause)
    end = round(time.time() - t0, 2); rec.stop()
    raw = subprocess.run(['ffmpeg', '-loglevel', 'error', '-i', str(tmp/f'v{warm}.mp4'), '-vf', 'scale=1:1',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    reds = [raw[i] for i in range(0, len(raw), 3)]
    changes = [round(i / 20, 2) for i in range(len(reds)) if i == 0 or abs(reds[i] - reds[i - 1]) > 20]
    good = len(changes) == 4 and all(abs(c - w) <= 0.15 for c, w in zip(changes, wrote)) and abs(len(reds) / 20 - end) <= 0.3
    print('PASS' if good else 'FAIL', f'warm-up {warm}s: wrote at {wrote}, stopped at {end} | video changes at {changes}, length {len(reds) / 20:.2f}s')
