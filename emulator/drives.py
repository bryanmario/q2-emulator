"""The drive lock: inside Q2 Emulator's private Linux, the PC's drives are read-only.

packaging/setup-rootfs.sh writes /etc/wsl.conf with [automount] options = "ro", so WSL mounts C:, D:,
E: ... read-only in the box: nothing that runs in it, by mistake or not, can change or delete a Windows
file through /mnt/c, /mnt/e and the rest. The emulator still has to write in two places, and only there:

  - the folder picked as the SD card (the player writes to its card), and
  - the folder screenshots and recordings are saved in.

writable(folder) mounts that one Windows folder again, writable, over itself (a drvfs mount of just that
folder), so its path stays the same and everything around it stays locked. Folders in the Linux itself,
and drives that are not locked (a developer's own WSL, where nothing here changes anything), are left as
they are.

Windows programs the box starts (PowerShell for the file pickers, Explorer) are not in the box: they run
as the user, outside the lock, as before.
"""
import os
import re
import subprocess

WINDOWS_FS = ('9p', 'drvfs', 'virtiofs')
POWERSHELL = '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
_ours = set()   # the folders writable() mounted, so release() never unmounts anything else


def _unescape(text):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), text)


def mounts():
    """[(mount point, per-mount options, file system type, source, super options)], from the kernel's list."""
    found = []
    try:
        with open('/proc/self/mountinfo') as f:
            for line in f:
                left, _, right = line.partition(' - ')
                a, b = left.split(), right.split()
                if len(a) < 6 or len(b) < 2: continue
                found.append((_unescape(a[4]), a[5].split(','), b[0], _unescape(b[1]), b[2] if len(b) > 2 else ''))
    except OSError:
        pass
    return found


def mount_of(path):
    """The mount a path lies on (the deepest mount point holding it), or None."""
    path = os.path.realpath(path)
    best = None
    for m in mounts():
        point = m[0]
        if path == point or path.startswith(point.rstrip('/') + '/'):
            if best is None or len(point) >= len(best[0]): best = m
    return best


def is_windows(m):
    """Whether a mount (from mounts()) is a Windows drive or folder."""
    return m is not None and (m[2] in ('drvfs', 'virtiofs') or (m[2] == '9p' and 'aname=drvfs' in m[4]))


def locked(path):
    """Whether path lies on a Windows drive mounted read-only (the lock)."""
    m = mount_of(path)
    return is_windows(m) and 'ro' in m[1]


def on_windows(path):
    return is_windows(mount_of(path))


def _windows_path(path):
    try:
        out = subprocess.run(['wslpath', '-w', str(path)], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if re.match(r'^[A-Za-z]:\\', out) else None   # a drive's folder, nothing else (no \\wsl...)


def make_folder(path):
    """Make a folder (and its parents). On a locked drive it is made by Windows itself, as the user, through
    PowerShell: the box cannot. True when the folder is there."""
    path = os.path.realpath(path)
    if os.path.isdir(path): return True
    parent = path
    while parent != '/' and not os.path.exists(parent): parent = os.path.dirname(parent)
    if not locked(parent):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return False
        return True
    base = _windows_path(parent)   # (wslpath names only what is there: the rest is added to it)
    if not base: return False
    rest = os.path.relpath(path, parent)
    if rest.startswith('..') or '\\' in rest: return False
    win = base.rstrip('\\') + '\\' + rest.replace('/', '\\')
    literal = win.replace("'", "''")
    try:
        subprocess.run([POWERSHELL, '-NoProfile', '-Command', f"[IO.Directory]::CreateDirectory('{literal}') | Out-Null"],
                       capture_output=True, timeout=30, cwd='/mnt/c')
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.isdir(path)


def writable(path):
    """Make one folder writable from the box, and nothing else. True when it can be written to (it was
    already, or now is), False when it could not be made so (the folder then stays read-only)."""
    path = os.path.realpath(path)
    if not os.path.isdir(path): return False
    if not locked(path): return True
    win = _windows_path(path)
    if not win: return False
    done = subprocess.run(['mount', '-t', 'drvfs', win, path, '-o', 'rw'], capture_output=True)
    if done.returncode or locked(path): return False
    _ours.add(path)
    return True


def release(path):
    """Undo writable(path): the folder is read-only again. Only a mount writable() made is ever undone."""
    path = os.path.realpath(path) if path else None
    if path not in _ours: return
    m = mount_of(path)
    if m and m[0] == path and is_windows(m):
        if subprocess.run(['umount', path], capture_output=True).returncode == 0:
            _ours.discard(path)
