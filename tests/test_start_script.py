"""Run q2emu's START script as PID 1 of an unprivileged sandbox, with a fake demo that uses busybox's own
reboot/poweroff, the way the firmware does. Run twice: with the host's busybox sh, and with the firmware's
own busybox sh under QEMU (what really runs in the box). Needs no root: a user namespace stands in."""
import os, pathlib, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'emulator'))
import q2emu

bb = shutil.which('busybox')
ROOTFS = pathlib.Path.home()/'.cache/q2emu/rootfs'
QEMU = q2emu.find_qemu()
assert q2emu.START.count('LD_PRELOAD=') == 1, 'prepare() relies on exactly one LD_PRELOAD='

def run(behaviour, shell):
    t = pathlib.Path(tempfile.mkdtemp())
    (t/'bin').mkdir()
    for tool in ('reboot', 'poweroff', 'sleep', 'mkdir', 'rm', 'cat', 'echo', 'date'):
        os.symlink(bb, t/'bin'/tool)
    # boot N does behaviour[N-1], then waits like the firmware; 'slowreboot' reboots after 21 s
    (t/'demo').write_text(f'''#!{bb} sh
n=$(( $(cat {t}/boots 2>/dev/null || echo 0) + 1 )); echo $n > {t}/boots
echo "boot $n" >> {t}/log
set -- {' '.join(behaviour)}
eval what=\\${{$n:-hang}}
case $what in
  reboot) sleep 1; reboot ;;
  slowreboot) sleep 21; reboot ;;
  off) sleep 1; poweroff ;;
  exit0) sleep 1; exit 0 ;;
  exit7) sleep 1; exit 7 ;;
esac
sleep 1000
''')
    (t/'demo').chmod(0o755)
    (t/'dbus').write_text(f'#!{bb} sh\nsleep 1000 &\n'); (t/'dbus').chmod(0o755)
    script = (q2emu.START
              .replace('/usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf', str(t/'dbus'))
              .replace('/release/bin/demo', str(t/'demo'))
              .replace('/etc/init.d/S11logo_display_shell', str(t/'no-loading-screen'))  # (it would wait for a /dev/fb0)
              .replace('/tmp/q2emu', str(t/'q2emu'))
              .replace('/var/run/dbus/', str(t) + '/'))
    (t/'start.sh').write_text(script)
    env = dict(os.environ, PATH=f'{t}/bin:/usr/bin:/bin')
    p = subprocess.run(['unshare', '--user', '--map-root-user', '--pid', '--fork', '--mount-proc',
                        *shell, str(t/'start.sh')], env=env, capture_output=True, text=True, timeout=180)
    log = (t/'log').read_text().split('\n')[:-1] if (t/'log').exists() else []
    return p.returncode, log, p.stdout.strip().splitlines()[-1:] + p.stderr.strip().splitlines()[-2:]

shells = {'host busybox': [bb, 'sh']}
if QEMU and (ROOTFS/'bin/busybox').exists():
    shells['firmware busybox under QEMU'] = [QEMU, '-L', str(ROOTFS), str(ROOTFS/'bin/busybox'), 'sh']
for name, shell in shells.items():
    print('==', name)
    for behaviour, want_code, want_boots in (
            (['reboot', 'off'], 0, 2),               # Restart soon after boot once, then Power off
            (['slowreboot', 'slowreboot', 'slowreboot', 'off'], 0, 4),  # restarts used normally: no loop
            (['reboot', 'reboot', 'reboot', 'reboot'], 97, 3),         # reboot loop: stopped after 3
            (['exit7'], 7, 1),                       # demo dies by itself
            (['exit0'], 98, 1),                      # demo ends cleanly by itself: not a power-off
    ):
        code, log, out = run(behaviour, shell)
        ok = code == want_code and len(log) == want_boots
        print('PASS' if ok else 'FAIL', behaviour, '-> exit', code, '| boots', len(log), '|', out)
