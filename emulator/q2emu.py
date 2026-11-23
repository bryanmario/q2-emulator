#!/usr/bin/env python3
"""Run the Q2's own firmware (stock or Q2 Pod) on a Linux PC and drive it.

The firmware's rootfs is unpacked into a work folder and its programs run there unmodified under
qemu-mipsel (chroot + binfmt_misc), with libq2emu.so (shim.c) preloaded to answer for the hardware:
the framebuffer is a file, and the keys, touch panel and wheel are UNIX sockets this script writes input
events to. Needs root, Linux, qemu-mipsel-static 11.1.3, squashfs-tools, clang and lld.

  sudo python3 emulator/q2emu.py --firmware update.tar                 # a plain window to click
  sudo python3 emulator/q2emu.py --firmware update.tar --script 'wait 8; wheel 2; key centre; wait 2; shot out.png'

Script words: wait S | key NAME [MS] | wheel N | tap X Y | swipe X1 Y1 X2 Y2 [MS] | shot FILE
(keys: centre return play next prev; coordinates are the upright screen's, 375x320)."""
import argparse, fcntl, hashlib, mmap, os, pathlib, re, shlex, shutil, signal, socket, struct, subprocess, sys, tarfile, threading, time, zipfile

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))
import drives  # the drive lock (drives.py)
XRES, YRES = 320, 375                      # the panel's own portrait frame; the screen is it turned upright
EVENT = struct.Struct('<iiHHi')            # struct input_event, MIPS32
EV_SYN, EV_KEY, EV_ABS, ABS_X, ABS_Y, ABS_PRESSURE, BTN_TOUCH = 0, 1, 3, 0, 1, 0x18, 0x14a
KEYS = {'centre': 116, 'return': 103, 'play': 108, 'next': 163, 'prev': 165}
DEVICES = {'keys': 'event1', 'touch': 'event2', 'wheel': 'event3', 'bt': 'event4'}
# As a real Q2's panels report (read from one, 2026-11-20: docs/DEVELOPMENT.md): the wheel and a moving
# finger every 12 ms; a spin of the wheel travels about 6 of its 200 units a report (500 a second); a tap
# lasts about 60 ms and a press of a button about 125; the touch panel says a finger is down with its
# pressure alone (no BTN_TOUCH).
WHEEL_TICK, WHEEL_STEP, SAMPLE_S = 28, 6, 0.012  # units a tick at 50% sensitivity; a report's travel and period
BINFMT = (r':q2mipsel:M::\x7fELF\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x08\x00:'
          r'\xff\xff\xff\xff\xff\xff\xff\x00\x00\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:{qemu}:F')
# The DAC's PCMs, silent: alsa.conf loads alsa.conf.d before the Q2's own /mnt/data/asound.conf.
# ponytail: a null PCM takes samples as fast as they are written, so a song's position runs ahead of the
# clock; a PCM paced to its rate (a shim over snd_pcm_writei, or an ALSA "file" plugin behind a rate limit).
ASOUND = '''pcm.!hw { @args [ CARD DEV SUBDEV ] @args.CARD { type string default "0" } @args.DEV { type integer default 0 } @args.SUBDEV { type integer default -1 } type null }
pcm.!plughw { @args [ CARD DEV SUBDEV ] @args.CARD { type string default "0" } @args.DEV { type integer default 0 } @args.SUBDEV { type integer default -1 } type null }
pcm.!default { type null }
'''
START = '''#!/bin/sh
# What S90play does before demo, without the hardware's own steps.
# This shell is PID 1 of the box, so it is also the Q2's init. The firmware's Restart (and a theme change,
# which restarts) runs busybox reboot, and Power off runs poweroff: they signal PID 1 (SIGTERM; SIGUSR1 or
# SIGUSR2) and then wait to be ended. So: a reboot ends everything else in the box and boots demo again,
# in the same window; a power-off ends the box with status 0; demo ending by itself ends it with demo's
# status (98 for a 0, so that 0 always means a power-off). Three reboots in a row, each within 20 s of
# its boot, are a reboot loop, not a user's Restart: the box ends with status 97 instead of looping.
export TSLIB_TSDEVICE=/dev/input/event2 TSLIB_CONFFILE=/etc/ts.conf TSLIB_PLUGINDIR=/usr/lib/ts
export Q2EMU_DIR=/tmp/q2emu ${Q2EMU_TRACE_ALL:+QEMU_STRACE=1}
mkdir -p /tmp/q2emu/cores && ulimit -c unlimited  # QEMU writes a guest core there when demo crashes
trap 'want=reboot' TERM
trap 'want=off' USR1 USR2
fast=0
while :; do
    want= status=
    rm -f /var/run/dbus/pid /var/run/dbus/system_bus_socket /tmp/q2emu/fb.pan /tmp/q2emu/fb.blank /tmp/q2emu/fb.drawn
    # The loading screen, by the Q2's own boot step: the firmware's picture, or the user's own
    # (/mnt/data/boot-logo.jpg). It stays up until demo draws. (Through env, and the name in a variable:
    # prepare() puts demo's settings before the one place this script names the preload variable.)
    p=LD_PRELOAD
    [ -x /etc/init.d/S11logo_display_shell ] && env "$p"=/lib/libq2emu.so /etc/init.d/S11logo_display_shell start
    /usr/bin/dbus-daemon --config-file=/etc/dbus-1/system.conf
    sleep 1
    # A boot animation plays to its end before demo takes the screen, as S90play waits for it on a
    # firmware that has one: its boot step keeps this flag while it plays (13 s at most).
    i=0; while [ -e /tmp/q2-bootanim ] && [ $i -lt 130 ]; do sleep 0.1; i=$((i+1)); done
    (cd /tmp/q2emu/cores && LD_PRELOAD=/lib/libq2emu.so exec /release/bin/demo) &
    demo=$! booted=$(date +%s)
    while [ -z "$want" ]; do  # a trapped signal interrupts wait; demo ending returns from it
        wait $demo; status=$?
        kill -0 $demo 2>/dev/null || break
    done
    kill -9 -1 2>/dev/null  # from PID 1: every other process in the box
    sleep 1
    case $want in
        reboot)
            echo "== q2emu: the firmware rebooted"
            if [ $(( $(date +%s) - booted )) -lt 20 ]; then fast=$((fast + 1)); else fast=0; fi
            if [ $fast -ge 3 ]; then echo "== q2emu: reboot loop, stopping"; exit 97; fi
            continue ;;
        off) echo "== q2emu: the firmware powered off"; exit 0 ;;
        *) echo "== q2emu: demo ended by itself, status $status"
           [ "${status:-1}" = 0 ] && status=98  # 0 stays a power-off's
           exit ${status:-1} ;;
    esac
done
'''
BATTERY = {  # sysfs files the firmware reads for the battery; fixed at 100%, full, 4.2 V
    'sys/devices/i2c-0/0-0062/cw2015_capacity': '100',
    'sys/devices/i2c-0/0-0062/cw2015_voltage': '4200',
    'sys/class/power_supply/battery/status': 'Full',
}
STRACE = 'QEMU_STRACE=1 '  # before LD_PRELOAD with --strace: demo's system calls, in its log
# The firmware's watchdog (started by demo) runs `pgrep /release/bin/demo` every few seconds and reboots
# when it finds nothing. Under QEMU, demo's process is named after QEMU, so it never finds it and would
# reboot the Q2 seconds after every boot. Its stand-in keeps the name (the firmware killalls it by name)
# and only waits; the start script already notices a demo that has ended. The original is kept beside it.
WATCHDOG = 'usr/bin/checkappprocess.sh'
WATCHDOG_STANDIN = '''#!/bin/sh
# q2emu stand-in (the original is checkappprocess.sh.q2emu-orig): see WATCHDOG in q2emu.py.
while :; do sleep 3600; done
'''
# Programs of the firmware that work hardware which is not here, each replaced in the emulator's own
# unpacked copy (never in a file that goes to a player) by a script of the same name: the firmware starts
# them and kills them by name. The original is kept beside each as NAME.q2emu-orig.
# Bluetooth, always: the firmware waits for rtk_hciattach to bring the radio up and, when it never does,
# shows "Bluetooth abnormal" and reboots, at every start once Bluetooth is switched on. The stand-in leaves
# the mark the real one leaves when the radio answers. No radio follows: Bluetooth switches on and finds nothing.
BT_STANDINS = {
    'usr/bin/rtk_hciattach': 'touch /tmp/rtk_init_success\nwhile :; do sleep 3600; done\n',
    'usr/bin/hciconfig': 'exit 0\n',
}
# Wi-Fi, while the emulator's internet is on (wifi.py answers as wpa_supplicant, on its socket; the box's
# wlan0 already has its address, from slirp4netns): wpa_supplicant only has to go to the background, udhcpc
# only to say where names are looked up.
WIFI_STANDINS = {
    'usr/sbin/wpa_supplicant': '( while :; do sleep 3600; done ) >/dev/null 2>&1 &\nexit 0\n',
    'sbin/udhcpc': 'echo "nameserver 10.0.2.3 # wlan0" > /tmp/resolv.conf\nwhile :; do sleep 3600; done\n',
}
STANDIN_MARK = '# q2emu stand-in'


def stand_in(root, name, body):
    """Put a stand-in script in place of the firmware's program name, keeping the original beside it."""
    path = root/name
    text = f'#!/bin/sh\n{STANDIN_MARK} (the original is {path.name}.q2emu-orig): see q2emu.py\n{body}'
    kept = path.with_name(path.name + '.q2emu-orig')
    if path.is_symlink() or path.exists():
        if not path.is_symlink() and path.read_bytes() == text.encode(): return
        if not (kept.is_symlink() or kept.exists()): path.rename(kept)   # (a busybox link is kept as the link)
        else: path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def original(root, name):
    """Undo stand_in: the firmware's own program is back."""
    path = root/name
    kept = path.with_name(path.name + '.q2emu-orig')
    if kept.is_symlink() or kept.exists():
        if path.is_symlink() or path.exists(): path.unlink()
        kept.rename(path)


CARD = [None]  # the card folder prepare() last made writable; Q2.stop() locks it again
SD_SLOT =['dev/mmcblk4'] + [f'dev/mmcblk4p{i}' for i in range(10)]  # the Q2's card slot, as the firmware looks for it
BINFMT_RULE = pathlib.Path('/proc/sys/fs/binfmt_misc/q2mipsel')


def run(*args, **kw):
    return subprocess.run([str(a) for a in args], check=True, **kw)


def lock(work):
    """Hold work/lock for as long as the returned file stays open: one emulator a work folder, since a second
    would share the framebuffer file and the input sockets, or delete the rootfs under the first."""
    work.mkdir(parents=True, exist_ok=True)
    f = open(work/'lock', 'w')
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError(f'another q2emu is already running on {work}')
    return f


def mounted(path):
    """Whether path is a mount point, by the kernel's own list. Not os.path.ismount: it compares device
    numbers, so it misses a folder bind-mounted from the same file system (an SD card folder kept in the
    Linux home). Missed, the card was mounted again over itself at every start, never unmounted, and
    remove_rootfs deleted the user's music through it."""
    want = os.path.realpath(path)
    try:
        with open('/proc/self/mountinfo') as f:  # field 5 is the mount point, its odd characters as \ooo
            return any(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]) == want for line in f)
    except OSError:
        return os.path.ismount(path)


def unmount(path):
    """Unmount everything mounted at path (mounts stack). True when nothing is mounted there any more."""
    for _ in range(16):
        if not mounted(path): return True
        if subprocess.run(['umount', str(path)], capture_output=True).returncode: break
    return not mounted(path)


def remove_tree(path, name):
    """Delete a folder the emulator made itself, after checking it is that folder and nothing else: its
    name must be the one expected, it must be at least three levels below /, and it must hold no mount (an
    SD card, a Windows folder) and lie on no Windows drive. Anything else is refused, never deleted."""
    raw = str(path)
    p = pathlib.Path(os.path.realpath(raw)) if raw.strip() else None
    if p is None or p.name != name or len(p.parts) < 4:
        raise RuntimeError(f'refusing to delete {raw!r}: not the emulator\'s own {name} folder')
    if not p.exists(): return
    if p.is_symlink() or not p.is_dir():
        raise RuntimeError(f'refusing to delete {p}: not a plain folder')
    if drives.on_windows(p):
        raise RuntimeError(f'refusing to delete {p}: it is on a Windows drive')
    inside = [m[0] for m in drives.mounts() if m[0] == str(p) or m[0].startswith(str(p) + '/')]
    if inside:
        raise RuntimeError(f'refusing to delete {p}: {inside[0]} is mounted inside it')
    shutil.rmtree(p)


def remove_rootfs(root):
    """Delete an unpacked rootfs, but never through a mount still inside it (mnt/mmc can be the user's own
    music folder, bind-mounted as the SD card)."""
    for m in 'proc', 'mnt/mmc':
        if not unmount(root/m): raise RuntimeError(f'{root/m} is still mounted; unmount it and retry')
    remove_tree(root, 'rootfs')


def symbol(elf, name):
    """The address of an exported symbol (.dynsym) in a 32-bit little-endian ELF, or None. The firmware's
    globals are looked up by name rather than written in as numbers, so another firmware build still works."""
    data = pathlib.Path(elf).read_bytes()
    shoff, = struct.unpack_from('<I', data, 32)
    shentsize, shnum = struct.unpack_from('<HH', data, 46)
    sections = [struct.unpack_from('<IIIIIIIIII', data, shoff + i * shentsize) for i in range(shnum)]
    for _, kind, _, _, off, size, link, _, _, entsize in sections:
        if kind != 11 or not entsize: continue  # SHT_DYNSYM
        strtab = sections[link][4]
        for i in range(size // entsize):
            st_name, st_value = struct.unpack_from('<II', data, off + i * entsize)
            end = data.index(b'\0', strtab + st_name)
            if data[strtab + st_name:end] == name.encode(): return st_value
    return None


def unpack(firmware, root):
    """The rootfs of an update.tar, or of a stock ZIP holding one, into root."""
    work = root.parent
    tar = firmware
    if zipfile.is_zipfile(firmware):
        with zipfile.ZipFile(firmware) as z:
            name = next(n for n in z.namelist() if n.endswith('update.tar'))
            tar = work/'update.tar'
            tar.write_bytes(z.read(name))
    with tarfile.open(tar) as t:
        (work/'rootfs.squashfs').write_bytes(t.extractfile('recovery-update/rootfs.squashfs').read())
    run('unsquashfs', '-q', '-f', '-no-xattrs', '-d', root, work/'rootfs.squashfs', stdout=subprocess.DEVNULL)


# Emulator-internal stub: the firmware's home_page_init registers ALL input handlers (keys, touch, wheel,
# idle, timers) only when strlen(g_product_sncode) == 14 — a per-unit serial filled by reading /dev/mtd5,
# hardware that doesn't exist in the emulator.  Without the handlers, nothing past the first screen responds.
# This patch makes the branch unconditional so the handlers are always registered.  It is applied to the
# emulator's own working copy of the binary, never to a file that ships to real hardware.
# The branch is found inside home_page_init by its exact instruction (in V1.36 and the Pod builds on it,
# home_page_init is at 0x534b6c and the branch at +0x40, 0x534bac).
_SERIAL_BEQ   = 0x10430007                      # beq $v0, $v1, +7  (original)
_SERIAL_ALWAYS = 0x10000007                     # beq $zero, $zero, +7  (patched: always taken)
_SERIAL_SCAN = 0x80                             # how far into home_page_init the gate is looked for


def _file_offset(data, vaddr):
    """The file offset of a virtual address inside one of the ELF's loaded segments, or None."""
    e_phoff, = struct.unpack_from('<I', data, 28)
    e_phentsize, e_phnum = struct.unpack_from('<HH', data, 42)
    for i in range(e_phnum):
        p_type, p_offset, p_vaddr, _, p_filesz = struct.unpack_from('<IIIII', data, e_phoff + i * e_phentsize)
        if p_type == 1 and p_vaddr <= vaddr < p_vaddr + p_filesz:
            return vaddr - p_vaddr + p_offset
    return None


def _patch_serial_check(demo):
    """Stub home_page_init's serial-length gate so input handlers are registered without /dev/mtd5."""
    start = symbol(demo, 'home_page_init')
    if start is None:
        raise RuntimeError('this firmware has no home_page_init symbol: the input gate cannot be found')
    data = bytearray(demo.read_bytes())
    base = _file_offset(data, start)
    if base is None:
        raise RuntimeError(f'home_page_init (0x{start:08x}) is outside the loaded segments')
    for off in range(base, base + _SERIAL_SCAN, 4):
        insn, = struct.unpack_from('<I', data, off)
        if insn == _SERIAL_ALWAYS: return  # already patched
        if insn == _SERIAL_BEQ:
            struct.pack_into('<I', data, off, _SERIAL_ALWAYS)
            demo.write_bytes(bytes(data))
            return
    raise RuntimeError(f'the serial-length gate was not found in home_page_init (0x{start:08x}): '
                       'this firmware build differs from the ones the emulator knows')


PREBUILT = HERE/'libq2emu.so'  # shim.c compiled ahead (tools/build_shim.py), with the hash of the shim.c it came from


def compile_shim(libc, target, out):
    """shim.c as a MIPS shared library, linked against a firmware's libc.so.6. Needs clang and lld."""
    obj = out/'shim.o'
    run('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mabi=32', '-mfp64', '-mnan=2008', '-mabs=2008',
        '-mabicalls', '-fPIC', '-Os', '-ffreestanding', '-fno-builtin', '-Wall', '-c', HERE/'shim.c', '-o', obj)
    run('ld.lld', '-m', 'elf32ltsmip', '-shared', '-z', 'noexecstack', '--hash-style=sysv', obj, libc, '-o', target)


def build_shim(root, out):
    """libq2emu.so into the rootfs: compiled here where clang and lld are installed (a developer's machine),
    else the copy compiled ahead, as long as it came from this very shim.c. The shim only asks libc for a
    few functions by name, so the copy linked against V1.36's libc serves the builds based on it."""
    target = root/'lib/libq2emu.so'
    if shutil.which('clang') and shutil.which('ld.lld'):
        return compile_shim(root/'lib/libc.so.6', target, out)
    stamp = PREBUILT.with_name(PREBUILT.name + '.sha256')
    digest = hashlib.sha256((HERE/'shim.c').read_bytes()).hexdigest()
    if not PREBUILT.exists() or not stamp.exists() or stamp.read_text().strip() != digest:
        raise RuntimeError('clang and lld are not installed, and there is no libq2emu.so compiled from this shim.c')
    shutil.copy(PREBUILT, target)
    target.chmod(0o755)


def find_qemu():
    """The qemu-mipsel-static to run the firmware with, or None.

    Q2EMU_QEMU wins. Otherwise the known-good 11.1.3 builds (this project's bin/ first) are tried before PATH, because sudo resets
    PATH and drops Q2EMU_QEMU unless it is passed on the sudo line, and a distro 8.2.2 found on PATH
    deadlocks under demo."""
    if os.environ.get('Q2EMU_QEMU'):
        return os.environ['Q2EMU_QEMU']
    homes = [pathlib.Path.home()]
    if os.environ.get('SUDO_USER'):
        import pwd
        try: homes.insert(0, pathlib.Path(pwd.getpwnam(os.environ['SUDO_USER']).pw_dir))
        except KeyError: pass
    candidates = [HERE.parent/'bin/qemu-mipsel-static', pathlib.Path('/opt/q2-qemu/bin/qemu-mipsel-static')]
    candidates += [h/'q2/archive/q2-qemu-bin/qemu-mipsel-static' for h in homes]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return shutil.which('qemu-mipsel-static')


# The QEMU of the measuring mode, with its counter and the libraries it needs: tools/build_count.sh makes
# the folder. The same QEMU as the usual one, built with plugins, so about a sixth slower; q2count.c counts
# the instructions each of the firmware's programs executes.
COUNT = HERE.parent/'bin/count'


def can_count():
    return (COUNT/'q2count-run').is_file() and (COUNT/'qemu-mipsel').is_file() and (COUNT/'libq2count.so').is_file()


def prepare(root, sd, strace=False, qemu_env='', internet=False, count=False):
    """The device nodes' stand-ins, the mounts and the binfmt_misc rule the firmware's programs run through."""
    # ponytail: Ubuntu 24.04's qemu-user-static 8.2.2 deadlocks under demo (every thread waits on one of
    # QEMU's own locks soon after the first frames); 11.1.3 built from source runs it. Q2EMU_QEMU names one.
    qemu = find_qemu()
    if not qemu: sys.exit('qemu-mipsel-static is missing: set Q2EMU_QEMU, or put the 11.1.3 build at /opt/q2-qemu/bin')
    # Measuring mode: the firmware's programs run under the counting QEMU (see COUNT), which lives in the
    # box as /q2emu-host with its libraries; the kernel starts each through its small launcher.
    host = root/'q2emu-host'
    remove_tree(host, 'q2emu-host')
    if count:
        shutil.copytree(COUNT, host)
        for f in [*host.iterdir(), COUNT/'q2count-run']: f.chmod(0o755)
        qemu = str(COUNT/'q2count-run')
    work = root/'tmp/q2emu/work'
    remove_tree(work, 'work')
    work.mkdir(parents=True)
    misc = BINFMT_RULE.parent
    if not (misc/'register').exists(): run('mount', '-t', 'binfmt_misc', 'binfmt_misc', misc)
    if BINFMT_RULE.exists() and f'interpreter {qemu}\n' not in BINFMT_RULE.read_text():
        BINFMT_RULE.write_text('-1')  # another QEMU's rule: drop it
    if not BINFMT_RULE.exists(): (misc/'register').write_text(BINFMT.format(qemu=qemu))
    dev = root/'dev'
    (dev/'input').mkdir(parents=True, exist_ok=True)
    for name, major, minor in ('null', 1, 3), ('zero', 1, 5), ('random', 1, 8), ('urandom', 1, 9), ('tty', 5, 0):
        if not (dev/name).exists(): run('mknod', '-m', '666', dev/name, 'c', major, minor)
    with open(dev/'fb0', 'wb') as f: f.truncate(XRES * YRES * 2 * 4)
    for name in 'shanling_dac', 'mcu': (dev/name).write_bytes(b'')
    for name in DEVICES.values():  # the shim opens these as connections to Q2's sockets; the node only has to exist
        (dev/'input'/name).unlink(missing_ok=True)
        (dev/'input'/name).write_bytes(b'')
    for d in 'tmp/q2emu', 'var/run/dbus', 'mnt/data', 'mnt/mmc': (root/d).mkdir(parents=True, exist_ok=True)
    for f in ('tmp/q2emu/ioctl.log', 'tmp/q2emu/fb.pan', 'tmp/q2emu/fb.blank', 'tmp/q2emu/fb.drawn',
              'var/run/dbus/system_bus_socket'):
        (root/f).unlink(missing_ok=True)
    # The battery, always full: the firmware reads the CW2015 fuel gauge from sysfs and, finding nothing,
    # takes the battery as empty and shows "Shutdown". Same values as the verified stub runs (stub-8..15).
    for path, value in BATTERY.items():
        (root/path).parent.mkdir(parents=True, exist_ok=True)
        (root/path).write_text(value + '\n')
    # The SD card. The firmware sees a card only with all three: the hotplug flag /tmp/mmc_add, the slot's
    # block-device nodes, and /mnt/mmc being a mount point of its own (it compares st_dev with /mnt's).
    for p in SD_SLOT + ['tmp/mmc_add']: (root/p).unlink(missing_ok=True)
    # a mount left by a run that was killed may be another folder's: never one card over another
    if not unmount(root/'mnt/mmc'): sys.exit(f"{root/'mnt/mmc'} is still mounted and could not be released")
    if sd:
        # the card's own folder writable, though its drive is locked (drives.py); the player writes to its card
        CARD[0] = sd
        if not drives.writable(sd): print(f'q2emu: the SD card {sd} can only be read: the player cannot write to it', file=sys.stderr)
        run('mount', '--bind', sd, root/'mnt/mmc')
        for p in SD_SLOT + ['tmp/mmc_add']: (root/p).write_bytes(b'')
    watchdog = root/WATCHDOG
    if watchdog.exists() and watchdog.read_text(errors='replace') != WATCHDOG_STANDIN:
        shutil.copy2(watchdog, watchdog.with_name(watchdog.name + '.q2emu-orig'))
        watchdog.write_text(WATCHDOG_STANDIN)
        watchdog.chmod(0o755)
    for name, body in BT_STANDINS.items(): stand_in(root, name, body)
    for f in 'tmp/rtk_init_success', 'tmp/resolv.conf': (root/f).unlink(missing_ok=True)
    for name, body in WIFI_STANDINS.items():
        if internet: stand_in(root, name, body)
        else: original(root, name)
    # Where names are looked up: the Q2's /etc/resolv.conf is a link to a file in /tmp that its DHCP client
    # writes. With the emulator's internet it is a file of its own, naming slirp4netns's resolver.
    resolv = root/'etc/resolv.conf'
    resolv.parent.mkdir(parents=True, exist_ok=True)
    if resolv.is_symlink() or resolv.exists(): resolv.unlink()
    if internet: resolv.write_text('nameserver 10.0.2.3\n')
    else: resolv.symlink_to('../tmp/resolv.conf')
    # g_backlight_status gates all input; the shim sets it on MCU ioctls, at the address found here.
    backlight = symbol(root/'release/bin/demo', 'g_backlight_status')
    env = (f'Q2EMU_BL_ADDR=0x{backlight:x} ' if backlight else '') + (STRACE if strace else '') + (qemu_env + ' ' if qemu_env else '')
    (root/'q2emu-start.sh').write_text(START.replace('LD_PRELOAD=', env + 'LD_PRELOAD='))
    (root/'usr/share/alsa/alsa.conf.d').mkdir(parents=True, exist_ok=True)
    (root/'usr/share/alsa/alsa.conf.d/99-q2emu.conf').write_text(ASOUND)
    (root/'q2emu-start.sh').chmod(0o755)


Q2_RAM_MB = 128  # the Q2's memory, all of it: its kernel and every program share it, and there is no swap
Q2_CORES = 2     # the X2000 has two; the box is kept to two of the PC's (fbdev.py), so a program that starts a
                 # thread a core (ffmpeg) starts as many as on a Q2, and takes the memory it would there
# Read from a Q2 (Q2 Pod 1.8.8, idle in its Games list, 2026-11-20; docs/DEVELOPMENT.md has the reading):
# its kernel leaves 117864 kB of the 128 MB (MemTotal), and with demo and hciplayer running 83028 kB
# were available. Here the same two hold 28 MB by this file's count (on the Q2: 11 MB of their own pages
# and 20 MB mapped from files), which puts what is unavailable besides the programs at 4 MB.
# ponytail: one reading, one state: a Q2 with a large library or a card app running may differ.
Q2_MEMTOTAL_KB, Q2_KERNEL_USE_MB = 117864, 4
# What a Q2 does a second, timed on one (the same work counted here): 1.0 to 1.3 G instructions of
# whole-number work (a shell loop, a tight loop), 0.31 G of decimal work, and far fewer when the work is
# spread over more memory than its cache holds (6 million scattered reads took 1.4 s). So a time worked
# out from this is the least it takes: work on decimals or on large data takes longer, and so does
# anything that waits for the card (which read 21 MB a second).
Q2_INSNS_PER_S = 1.08e9


def meminfo(used_mb):
    """/proc/meminfo as a Q2 whose programs hold used_mb would give it. The box's own would be the PC's:
    gigabytes free, where Q2 Pod's video player and its chapter reader ask MemAvailable before they start."""
    total = Q2_MEMTOTAL_KB
    free = max(0, total - int((used_mb + Q2_KERNEL_USE_MB) * 1024))
    rows = [('MemTotal', total), ('MemFree', free), ('MemAvailable', free), ('Buffers', 0), ('Cached', 0),
            ('SwapCached', 0), ('SwapTotal', 0), ('SwapFree', 0)]
    return ''.join(f'{name + ":":<16}{kb:>8} kB\n' for name, kb in rows)


def box_programs(root):
    """The firmware's programs running in the box of this rootfs: [(pid, name)], the name as the Q2's ps
    would give it (each is a QEMU here, running the program its second argument names)."""
    root, found = os.path.realpath(root), []
    for d in os.listdir('/proc'):
        if not d.isdigit(): continue
        try:
            if os.readlink(f'/proc/{d}/root') != root: continue
            args = open(f'/proc/{d}/cmdline', 'rb').read().split(b'\0')
        except OSError: continue
        if len(args) < 2 or b'qemu-mipsel' not in args[0]: continue
        words = [a.decode(errors='replace') for a in args[1:] if a]
        while len(words) > 2 and words[0] in ('-plugin', '-E'): words = words[2:]  # the counting QEMU's own
        if not words: continue
        shell = os.path.basename(words[0]) in ('sh', 'busybox', 'ash') and len(words) > 1 and words[1].startswith('/')
        found.append((int(d), os.path.basename(words[1] if shell else words[0])))
    return found


def guest_memory_kb(pid):
    """How much memory the firmware's program in this QEMU holds, in kB: its share (Pss, so a library ten
    programs share counts once) of the pages in the program's own address space. QEMU keeps a 32-bit
    program's 2 GB at the bottom of its own, so those are the mappings below 0x80000000; QEMU's own
    memory (its code, its heap, the translated code) lies far above and is not counted: a Q2 has none of it."""
    total = 0
    try:
        with open(f'/proc/{pid}/smaps') as f:
            ours = False
            for line in f:
                head = line.split(None, 2)
                if len(head) >= 2 and '-' in head[0] and len(head[1]) == 4 and head[1][3] in 'ps':  # a mapping's first line
                    ours = int(head[0].split('-')[0], 16) < 0x80000000
                elif ours and line.startswith('Pss:'):
                    total += int(line.split()[1])
    except (OSError, ValueError): pass
    return total


# sh -c TETHER % 'unshare ...' <the box's command>, with a pipe as stdin: runs the box, and kills it when
# the pipe closes ($$ is the shell that becomes unshare; unshare's --kill-child does the rest).
TETHER = 'exec 3<&0; ( cat <&3 >/dev/null 2>&1; kill -KILL $$ ) & exec %s sh -c "$0" 3<&- </dev/null'


class Q2:
    """The running firmware: its screen and its input devices."""

    def __init__(self, root, log, internet=False):
        self.root = root
        self.wifi = self._slirp = None
        # One listening socket a device: the shim connects each open of /dev/input/eventN to it, and an
        # event goes to every connection, as evdev gives every opener the whole stream.
        self.servers, self.readers = {}, {k: [] for k in DEVICES}
        for k, n in DEVICES.items():
            path = root/'tmp/q2emu'/f'{n}.sock'
            path.unlink(missing_ok=True)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(16)
            server.setblocking(False)
            self.servers[k] = server
        # A box of its own. The firmware runs as root and treats the machine as its Q2: its watchdog
        # (checkappprocess.sh) and power-off run `reboot`, it sets the clock, loads modules and configures
        # the network. So: its own PID, network, IPC and UTS namespaces (a reboot reaches only this script's
        # child, and Wi-Fi finds no interface), /proc/sysrq-trigger and /proc/sys out of reach, and no
        # capability to reboot, set the clock, load modules or touch raw I/O.
        r = shlex.quote(str(root))
        drop = '-sys_boot,-sys_time,-sys_module,-sys_rawio,-sys_admin,-net_admin,-mknod'
        self.used = (0, [])   # memory(), as last measured by _account
        self.peak = (0, [])   # and the most it has been since the firmware started
        self.work, self._seen_work, self._work_gone = None, {}, 0   # _count(), likewise
        (root/'tmp/q2emu').mkdir(parents=True, exist_ok=True)
        (root/'tmp/q2emu/meminfo').write_text(meminfo(30))
        inside = (f'mount -t proc proc {r}/proc && {{ [ ! -e {r}/proc/sysrq-trigger ] || mount --bind /dev/null {r}/proc/sysrq-trigger; }} && '
                  f'mount --bind {r}/tmp/q2emu/meminfo {r}/proc/meminfo && '
                  f'mount --bind {r}/proc/sys {r}/proc/sys && mount -o remount,ro,bind {r}/proc/sys && '
                  f'exec setpriv --bounding-set {drop} --inh-caps {drop} chroot {r} /q2emu-start.sh')
        # A tether: beside the box, `cat` reads a pipe whose only write end is this program's. However this
        # program ends (closed, killed, its terminal gone), the pipe closes, cat returns and the box is
        # killed. Without it a firmware outlives its window, its input threads spinning on closed sockets.
        # The box's first step is fbdev.py: it puts the filter on the box that hands the screen's ioctls to
        # this program (fbdev.Screen answers them, for the programs shim.c cannot be loaded into), and passes
        # the filter's listening end back over a socket.
        import fbdev
        ours, theirs = socket.socketpair()
        self.proc = subprocess.Popen([sys.executable, str(HERE/'fbdev.py'), str(theirs.fileno()),
                                      'sh', '-c', TETHER % 'unshare --pid --fork --mount --net --ipc --uts --kill-child',
                                      inside], stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, pass_fds=(theirs.fileno(),))
        theirs.close()
        self.screen = None
        try:
            ours.settimeout(15)
            _, fds, _, _ = socket.recv_fds(ours, 1, 1)
            if fds: self.screen = fbdev.Screen(fds[0], root/'dev/fb0', self._panned, self._blanked)
        except OSError as e:
            print('q2emu: the screen filter is not there:', e, file=sys.stderr, flush=True)
        ours.close()
        self._ended = threading.Event()
        threading.Thread(target=self._account, daemon=True).start()
        if internet:
            import wifi
            self.wifi = wifi.Wifi(root, open(root.parent/'wifi.log', 'w'))
            self.wifi.start()
            threading.Thread(target=self._connect, args=(log,), daemon=True).start()
        self.wheel_x = 100
        self._fb, self._seen = None, None  # frame(): the mapped framebuffer, and (bytes, picture) last made
        self._emit_lock = threading.Lock()  # emit() may be called from more than one thread (the GUI's)

    def memory(self):
        """What the firmware's programs hold of the Q2's memory, about: (MB in all, [(name, MB), ...] the
        largest first). Not the kernel's part, which a Q2 also takes from its 128 MB."""
        by_name = {}
        for pid, name in box_programs(self.root):
            by_name[name] = by_name.get(name, 0) + guest_memory_kb(pid) / 1024
        rows = sorted(by_name.items(), key=lambda r: -r[1])
        return sum(mb for _, mb in rows), rows

    def _count(self):
        """The instructions the firmware's programs have executed since it was loaded, in measuring mode
        (each keeps its count in a file, see q2count.c); None otherwise. Files are read again only when
        they have changed, and one that is gone (the box's /tmp cleared) keeps what it last said."""
        folder, seen, live = self.root/'tmp/q2emu/work', self._seen_work, set()
        try: entries = list(os.scandir(folder))
        except OSError: entries = []
        for e in entries:
            try: stamp = e.stat().st_mtime_ns
            except OSError: continue
            live.add(e.name)
            if seen.get(e.name, (None, 0))[0] != stamp:
                try:
                    with open(e.path, 'rb') as f: data = f.read(8)
                except OSError: continue
                if len(data) == 8: seen[e.name] = (stamp, struct.unpack('<Q', data)[0])
        for name in [n for n in seen if n not in live]: self._work_gone += seen.pop(name)[1]
        return self._work_gone + sum(v for _, v in seen.values()) if (seen or self._work_gone) else None

    def _account(self):
        """Every second: measure what the firmware's programs hold, and let the box's /proc/meminfo say
        what is left of a Q2's memory (written in place: the box has this very file mounted)."""
        path = self.root/'tmp/q2emu/meminfo'
        turn = 0
        while not self._ended.wait(0.25) and self.proc.poll() is None:
            turn += 1
            try:
                if turn % 4 == 0: self.work = self._count()
                self.used = self.memory()
                if self.used[0] > self.peak[0]: self.peak = self.used
                with open(path, 'r+') as f:
                    f.write(meminfo(self.used[0]))
                    f.truncate()
            except OSError: pass

    def _tell(self, name, value):
        """As shim.c's tell(): a number in a file of the box's /tmp/q2emu."""
        try: (self.root/'tmp/q2emu'/name).write_text(f'{value & 0xffffffff:08x}\n')
        except OSError: pass

    def _panned(self, yoffset): self._tell('fb.pan', yoffset)   # (fbdev.Screen's thread)
    def _blanked(self, how): self._tell('fb.blank', how)

    def _connect(self, log):
        """The emulator's internet: the box has a network of its own with nothing in it, and slirp4netns
        puts one interface there, named wlan0 as the Q2's Wi-Fi is, that leads out through this PC as any
        program's traffic does (no rights or settings of the PC are touched; the PC's own local services,
        127.0.0.1, are out of the box's reach). It ends with the box."""
        mine = os.readlink('/proc/self/ns/net')
        for _ in range(100):  # until unshare has made the box's network
            try:
                if os.readlink(f'/proc/{self.proc.pid}/ns/net') != mine: break
            except OSError: return
            time.sleep(0.05)
        else: return
        try:
            self._slirp = subprocess.Popen(['slirp4netns', '--configure', '--mtu=1500', '--disable-host-loopback',
                                            str(self.proc.pid), 'wlan0'], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        except OSError as e:
            print('q2emu: no internet for the player:', e, file=sys.stderr, flush=True)

    def stop(self):
        """End the box (unshare's --kill-child takes the namespace's init with it, and so every process in
        it), then undo what prepare() left on the host: the SD card's bind mount and the binfmt_misc rule.
        SIGUSR2 first, not SIGTERM: the start script takes SIGTERM as the firmware's reboot, and unshare
        --fork passes SIGTERM on rather than dying of it, but SIGUSR2 is a power-off to both."""
        for sig in (signal.SIGUSR2, signal.SIGKILL):
            try: os.killpg(self.proc.pid, sig)
            except ProcessLookupError: break
            try:
                self.proc.wait(timeout=3)
                break
            except subprocess.TimeoutExpired: pass
        try: self.proc.stdin.close()  # lets the tether go
        except OSError: pass
        for s in [*self.servers.values(), *(r for rs in self.readers.values() for r in rs)]:
            s.close()
        self._ended.set()
        if self.screen: self.screen.stop()
        if self.wifi: self.wifi.stop()
        if self._slirp and self._slirp.poll() is None:
            self._slirp.terminate()
            try: self._slirp.wait(timeout=3)
            except subprocess.TimeoutExpired: self._slirp.kill()
        fb, self._fb = self._fb, None
        if fb is not None:
            try: fb.close()
            except (OSError, ValueError, BufferError): pass
        unmount(self.root/'mnt/mmc')
        drives.release(CARD[0])  # the card's folder read-only again, as its drive is (drives.py)
        try: BINFMT_RULE.write_text('-1')
        except OSError: pass

    def emit(self, device, *events):
        with self._emit_lock:
            t = time.time()
            sec, usec = int(t), int(t % 1 * 1e6)
            data = b''.join(EVENT.pack(sec, usec, *e) for e in (*events, (EV_SYN, 0, 0)))
            while True:  # openers since the last event
                try: reader = self.servers[device].accept()[0]
                except BlockingIOError: break
                reader.setblocking(False)
                self.readers[device].append(reader)
            for reader in self.readers[device][:]:
                try: reader.send(data)
                except BlockingIOError: pass  # an opener that never reads: its buffer is full, as evdev's would drop
                except OSError: self.readers[device].remove(reader)  # closed its end

    def press(self, name, down=True):
        """A button going down, or coming up again. The firmware acts when it comes up (the centre button
        about a quarter of a second after that), so how long it is held is how late the player answers."""
        self.emit('keys', (EV_KEY, KEYS[name], 1 if down else 0))

    def key(self, name, ms=125):
        self.press(name)
        time.sleep(ms / 1000)
        self.press(name, False)

    def touch(self, x, y, down=True, first=False):
        """Upright screen coordinates; the panel's ABS_X is 319 - y and its ABS_Y is x."""
        if not down: return self.emit('touch', (EV_ABS, ABS_PRESSURE, 0))
        events = [(EV_ABS, ABS_X, XRES - 1 - y), (EV_ABS, ABS_Y, x)]
        if first: events += [(EV_ABS, ABS_PRESSURE, 1)]
        self.emit('touch', *events)

    def tap(self, x, y, ms=60):
        self.touch(x, y, first=True)
        time.sleep(ms / 1000)
        self.touch(x, y, down=False)

    def swipe(self, x1, y1, x2, y2, ms=300):
        n = max(1, int(ms / 1000 / SAMPLE_S))
        for i in range(n + 1):
            self.touch(x1 + (x2 - x1) * i // n, y1 + (y2 - y1) * i // n, first=not i)
            time.sleep(SAMPLE_S)
        self.touch(x2, y2, down=False)

    def wheel(self, ticks):
        """Positive is down/next: x falls. A touch, the travel in samples, the last repeated, a lift."""
        travel = round((abs(ticks) + .5) * WHEEL_TICK)
        n = -(-travel // WHEEL_STEP)
        sign = -1 if ticks > 0 else 1
        x = self.wheel_x
        self.emit('wheel', (EV_ABS, ABS_X, x), (EV_ABS, ABS_PRESSURE, 1))
        points = [(x + sign * (travel * i // n) - 1) % 200 + 1 for i in range(1, n + 1)]
        for p in points + points[-1:] * 7:
            time.sleep(SAMPLE_S)
            self.emit('wheel', (EV_ABS, ABS_X, p))
        self.emit('wheel', (EV_ABS, ABS_PRESSURE, 0))
        self.wheel_x = points[-1]

    def _told(self, name):
        """A number the shim wrote for us (shim.c tell()), or None."""
        try: return int((self.root/'tmp/q2emu'/name).read_text(), 16)
        except (OSError, ValueError): return None

    def started(self):
        """demo has shown a picture of its own: the boot's loading screen is over."""
        return self._told('fb.drawn') is not None

    def screen_off(self):
        """The firmware has switched its panel off (its idle timer). The frame stays as it was; the wheel
        then sets the volume and touches are ignored, until the centre button (the Q2's power key) wakes it."""
        return bool(self._told('fb.blank'))

    def frame(self):
        """The page the panel shows, upright, as a PIL image."""
        from PIL import Image
        try: page = int((self.root/'tmp/q2emu/fb.pan').read_text(), 16) // YRES
        except (OSError, ValueError): page = 0
        if self._fb is None:  # the framebuffer's file, mapped once: looking at it costs a copy, not a read
            with open(self.root/'dev/fb0', 'rb') as f: self._fb = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        size = XRES * YRES * 4
        data = self._fb[page * size:(page + 1) * size]
        seen = self._seen
        if seen and seen[0] == data: return seen[1]  # nothing new: the very same picture (callers only read it)
        img = Image.frombuffer('RGBA', (XRES, YRES), data, 'raw', 'BGRA', 0, 1).convert('RGB').rotate(90, expand=True)
        self._seen = (data, img)
        return img

    def script(self, text):
        for step in filter(None, (s.split() for s in text.split(';'))):
            word, args = step[0], step[1:]
            if word == 'wait': time.sleep(float(args[0]))
            elif word == 'key': self.key(args[0], *map(int, args[1:]))
            elif word == 'wheel': self.wheel(int(args[0]))
            elif word == 'tap': self.tap(*map(int, args))
            elif word == 'swipe': self.swipe(*map(int, args))
            elif word == 'shot': self.frame().save(args[0])
            else: sys.exit(f'unknown script word: {word}')


def window(q2):
    """The screen at twice its size, the mouse as a finger, and the keys below."""
    import tkinter as tk
    from PIL import ImageTk
    scale = 2
    top = tk.Tk()
    top.title('Q2 firmware (qemu)')
    canvas = tk.Canvas(top, width=YRES * scale, height=XRES * scale, highlightthickness=0)
    canvas.pack()
    image = canvas.create_image(0, 0, anchor='nw')
    state = {'photo': None, 'down': False}

    def point(e): return max(0, min(YRES - 1, e.x // scale)), max(0, min(XRES - 1, e.y // scale))
    def press(e): state['down'] = True; q2.touch(*point(e), first=True)
    def move(e):
        if state['down']: q2.touch(*point(e))
    def release(e): state['down'] = False; q2.touch(0, 0, down=False)
    canvas.bind('<ButtonPress-1>', press)
    canvas.bind('<B1-Motion>', move)
    canvas.bind('<ButtonRelease-1>', release)
    bar = tk.Frame(top)
    bar.pack(fill='x')
    for label, action in (('Wheel up', lambda: q2.wheel(-1)), ('Wheel down', lambda: q2.wheel(1)),
                          ('Centre', lambda: q2.key('centre')), ('Return', lambda: q2.key('return')),
                          ('Prev', lambda: q2.key('prev')), ('Play', lambda: q2.key('play')),
                          ('Next', lambda: q2.key('next'))):
        tk.Button(bar, text=label, command=action).pack(side='left', expand=True, fill='x')
    for sym, action in (('<Up>', lambda e: q2.wheel(-1)), ('<Down>', lambda e: q2.wheel(1)),
                        ('<Return>', lambda e: q2.key('centre')), ('<Escape>', lambda e: q2.key('return')),
                        ('<BackSpace>', lambda e: q2.key('return')), ('<space>', lambda e: q2.key('play')),
                        ('<Left>', lambda e: q2.key('prev')), ('<Right>', lambda e: q2.key('next'))):
        top.bind(sym, action)
    canvas.bind('<MouseWheel>', lambda e: q2.wheel(-1 if e.delta > 0 else 1))
    canvas.bind('<Button-4>', lambda e: q2.wheel(-1))
    canvas.bind('<Button-5>', lambda e: q2.wheel(1))

    def refresh():
        frame = q2.frame().resize((YRES * scale, XRES * scale))
        state['photo'] = ImageTk.PhotoImage(frame)
        canvas.itemconfigure(image, image=state['photo'])
        if q2.proc.poll() is None: top.after(50, refresh)
        else: top.title(f'Q2 firmware (qemu): demo ended, status {q2.proc.returncode}')
    refresh()
    top.mainloop()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--firmware', type=pathlib.Path, help='an update.tar, or a stock ZIP holding one')
    ap.add_argument('--work', type=pathlib.Path, default=pathlib.Path.home()/'.cache/q2emu',
                    help='where the rootfs is unpacked and kept (default: %(default)s)')
    ap.add_argument('--sd-card', type=pathlib.Path, help='a folder to mount as the card (/mnt/mmc)')
    ap.add_argument('--script', help='input and screenshots to run instead of opening a window')
    ap.add_argument('--strace', action='store_true', help="log demo's system calls (QEMU_STRACE) to demo.log")
    ap.add_argument('--qemu-env', default='', metavar='NAME=VALUE', help="QEMU settings for demo alone, such as QEMU_GDB=/tmp/q2emu/gdb.sock (demo then waits for gdb-multiarch's target remote on ROOTFS/tmp/q2emu/gdb.sock)")
    ap.add_argument('--fresh', action='store_true', help='unpack the firmware again, dropping the saved settings')
    a = ap.parse_args()
    if os.geteuid(): sys.exit('q2emu needs root: chroot, mounts and binfmt_misc')
    root = a.work/'rootfs'
    try: held = lock(a.work)  # taken before the rootfs is touched, held until exit  # noqa: F841
    except RuntimeError as e: sys.exit(str(e))
    if a.fresh and root.exists(): remove_rootfs(root)
    if not root.exists():
        if not a.firmware: sys.exit('--firmware is needed the first time')
        unpack(a.firmware, root)
    _patch_serial_check(root/'release/bin/demo')
    build_shim(root, a.work)
    prepare(root, a.sd_card, a.strace, a.qemu_env)
    log = open(a.work/'demo.log', 'wb')
    q2 = Q2(root, log)
    try:
        if a.script: q2.script(a.script)
        else: window(q2)
    finally:
        q2.stop()


if __name__ == '__main__':
    main()
