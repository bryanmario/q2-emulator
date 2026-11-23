"""Q2.stop()'s way of ending the box (SIGTERM to the process group, then SIGKILL) must still end it now
that PID 1 traps SIGTERM as a reboot."""
import os, pathlib, shutil, signal, subprocess, sys, tempfile, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import q2emu, emulator_gui as g
bb = shutil.which('busybox')
t = pathlib.Path(tempfile.mkdtemp())
(t/'demo').write_text(f'#!{bb} sh\nsleep 1000\n'); (t/'demo').chmod(0o755)
(t/'dbus').write_text(f'#!{bb} sh\nsleep 1000 &\n'); (t/'dbus').chmod(0o755)
(t/'start.sh').write_text(q2emu.START.replace('/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf', str(t/'dbus'))
                          .replace('/release/bin/demo', str(t/'demo')).replace('/tmp/q2emu', str(t/'q2emu'))
                          .replace('/var/run/dbus/', str(t) + '/'))
p = subprocess.Popen(['unshare', '--user', '--map-root-user', '--pid', '--fork', '--mount-proc', '--kill-child',
                      bb, 'sh', str(t/'start.sh')], start_new_session=True)
time.sleep(3)
start = time.time()
for sig in (signal.SIGUSR2, signal.SIGKILL):  # exactly Q2.stop's sequence
    try: os.killpg(p.pid, sig)
    except ProcessLookupError: break
    try: p.wait(timeout=3); break
    except subprocess.TimeoutExpired: pass
left = subprocess.run(['pgrep', '-f', str(t/'demo')], capture_output=True, text=True).stdout.split()
print('PASS' if p.poll() is not None and not left else 'FAIL',
      f'window close ends the box in {time.time() - start:.1f}s, leftovers: {left}')

# GUI: exit status 0 reads as the player's own power-off; anything else as a stop
class P:
    def __init__(s, c): s.returncode = c
    def poll(s): return s.returncode
for code, want in ((0, 'off'), (7, 'stopped'), (98, 'stopped')):
    r = g.FirmwareRunner(tempfile.mkdtemp())
    r.q2 = type('Q', (), {'proc': P(code), 'stop': lambda s: None})(); r.state = 'running'; r.poll()
    print('PASS' if r.state == want else 'FAIL', f'exit {code} -> {r.state}: {r.message}')

# The tether: when the program that started the box is gone (its end of the pipe closes), the box ends too.
p = subprocess.Popen(['sh', '-c', q2emu.TETHER % 'unshare --user --map-root-user --pid --fork --kill-child',
                      f'sleep 1000 # {t}/tethered'], stdin=subprocess.PIPE, start_new_session=True)
time.sleep(1.5)
alive = p.poll() is None and subprocess.run(['pgrep', '-f', f'{t}/tethered'], capture_output=True, text=True).stdout.split()
p.stdin.close()  # what the kernel does when this program dies, however it dies
try: p.wait(timeout=5)
except subprocess.TimeoutExpired: pass
time.sleep(0.3)
left = subprocess.run(['pgrep', '-f', f'{t}/tethered'], capture_output=True, text=True).stdout.split()
print('PASS' if alive and p.poll() is not None and not left else 'FAIL',
      f'a firmware cannot outlive the program that started it: was running {bool(alive)}, leftovers after: {left}')
# A card folder on the same file system as the rootfs (in the Linux home): os.path.ismount misses such a
# mount; missed, the card was deleted with the rootfs. In a user namespace, where mounting needs no root.
probe = f"""
import os, pathlib, subprocess, sys
sys.path.insert(0, {str(q2emu.HERE)!r}); import q2emu
t = pathlib.Path({str(t)!r}); root = t/'w2'/'rootfs'; card = t/'card'   # (named rootfs: remove_tree deletes nothing else)
(root/'mnt/mmc').mkdir(parents=True); (root/'proc').mkdir(); card.mkdir(); (card/'song.flac').write_bytes(b'music')
for _ in range(3): subprocess.run(['mount', '--bind', str(card), str(root/'mnt/mmc')], check=True)   # stacked, as it was
print('seen', q2emu.mounted(root/'mnt/mmc'), 'ismount', os.path.ismount(root/'mnt/mmc'))
q2emu.remove_rootfs(root)
print('rootfs gone', not root.exists(), 'song kept', (card/'song.flac').read_bytes() == b'music')
"""
out = subprocess.run(['unshare', '--user', '--map-root-user', '--mount', sys.executable, '-I', '-c', probe],
                     capture_output=True, text=True)
print('PASS' if 'seen True' in out.stdout and 'rootfs gone True song kept True' in out.stdout else 'FAIL',
      'a card folder on the rootfs\'s own file system is seen as mounted and survives the rootfs being removed:',
      out.stdout.strip().replace('\n', '; '), out.stderr.strip()[-200:])
