"""Non-root checks of the drive lock (emulator/drives.py) and of the emulator's own deletes. Uses a temporary
folder only; deletes nothing outside it."""
import os, pathlib, re, sys, tempfile
HERE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'emulator'))
import drives, q2emu

ok = lambda cond, what: print(('PASS ' if cond else 'FAIL ') + what)
tmp = pathlib.Path(tempfile.mkdtemp())


def refused(path, name):
    try:
        q2emu.remove_tree(path, name)
    except RuntimeError as e:
        return 'refusing' in str(e)
    return False


# 1. The lock's text is the same where the Linux is made and where installs are updated
conf = lambda text: re.search(r"cat > /etc/wsl.conf <<'EOF'\n(.*?)\nEOF", text, re.S)[1]
made = conf((HERE / 'packaging/setup-rootfs.sh').read_text())
updated = conf((HERE / 'packaging/install.sh').read_text())
ok(made == updated, 'setup-rootfs.sh and install.sh write the same wsl.conf')
ok('[automount]' in made and 'options = "ro"' in made, 'wsl.conf mounts the drives read-only')
ok('install.sh' in (HERE / 'packaging/build.ps1').read_text(), 'build.ps1 puts install.sh in the app')
ok('install.sh' in (HERE / 'packaging/launcher.cs').read_text() and 'rm -rf' not in (HERE / 'packaging/launcher.cs').read_text(),
   'launcher.cs installs through install.sh and deletes nothing itself')
for f in 'packaging/install.sh', 'packaging/setup-rootfs.sh':
    ok(b'\r' not in (HERE / f).read_bytes(), f'{f} has Linux line ends')

# 2. remove_tree deletes the emulator's own folder, and refuses everything else
root = tmp / 'w' / 'rootfs'; (root / 'etc').mkdir(parents=True); (root / 'etc/x').write_text('x')
q2emu.remove_tree(root, 'rootfs'); ok(not root.exists(), 'its own rootfs folder is deleted')
q2emu.remove_tree(root, 'rootfs'); ok(True, 'a folder already gone is no error')
keep = tmp / 'w' / 'music'; keep.mkdir(); (keep / 'song.flac').write_text('x')
ok(refused(keep, 'rootfs') and (keep / 'song.flac').exists(), 'a folder of another name is refused, and kept')
for bad in ('', ' ', '/', '/rootfs', '/tmp/rootfs'):
    ok(refused(bad, 'rootfs'), f'{bad!r} is refused (empty, the top, or too near it)')
link = tmp / 'w' / 'l' / 'rootfs'; link.parent.mkdir(); link.symlink_to(keep)
ok(refused(link, 'rootfs') and (keep / 'song.flac').exists(), 'a link pointing elsewhere is refused (its target has another name)')
win = tmp / 'w' / 'win' / 'rootfs'; win.mkdir(parents=True)
real = drives.on_windows
drives.on_windows = lambda p: True
ok(refused(win, 'rootfs') and win.exists(), 'a folder on a Windows drive is refused')
drives.on_windows = real
mnt = tmp / 'w' / 'm' / 'rootfs'; (mnt / 'mnt/mmc').mkdir(parents=True)
real_mounts = drives.mounts
drives.mounts = lambda: [(str(mnt / 'mnt/mmc'), ['rw'], 'ext4', '/dev/x', '')]
ok(refused(mnt, 'rootfs') and mnt.exists(), 'a folder with a mount inside is refused')
drives.mounts = real_mounts

# 3. drives: what counts as locked
fake = [('/', ['rw'], 'ext4', '/dev/sdc', 'rw'),
        ('/mnt/c', ['ro', 'noatime'], '9p', 'C:\\', 'ro,aname=drvfs;path=C:\\'),
        ('/mnt/e', ['ro', 'noatime'], '9p', 'E:\\', 'ro,aname=drvfs;path=E:\\'),
        ('/mnt/e/card', ['rw', 'relatime'], '9p', 'E:\\card', 'rw,aname=drvfs;path=E:\\card'),
        ('/mnt/wslg', ['rw'], 'tmpfs', 'none', 'rw')]
drives.mounts = lambda: fake
real_realpath = os.path.realpath
os.path.realpath = lambda p: str(p)
ok(drives.locked('/mnt/c/Users/x'), 'a file on C: is locked')
ok(not drives.locked('/mnt/e/card/song.flac'), 'the SD card folder made writable is not')
ok(drives.locked('/mnt/e/cards/other'), 'a folder beside it is still locked')
ok(not drives.locked('/root/.cache/q2emu/rootfs'), 'the Linux itself is not locked')
ok(not drives.on_windows('/mnt/wslg/x'), 'WSLg\'s own folder is not a Windows drive')
ok(drives.writable('/root') is True or not os.path.isdir('/root'), 'a Linux folder needs nothing done')
os.path.realpath = real_realpath
drives.mounts = real_mounts
drives.release('/mnt/c'); ok(True, 'release() of a folder it never mounted does nothing')
