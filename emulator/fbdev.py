"""The Q2's screen, answered for every program of the firmware, however it was built.

The panel is a file here (dev/fb0 in the unpacked firmware), and a program that asks the panel something
(its size, to show its other page) asks with an ioctl, which a file does not answer. For the firmware's
own programs shim.c answers, preloaded into them. But a program linked statically takes no preload: the
games and apps of Q2 Pod's App Store are built that way, so they found no screen and gave up.

So the questions are caught one level down. The box is started with a seccomp filter that hands the
framebuffer's ioctls, and no others, to the emulator instead of the kernel (a "user notification"); every
program in the box inherits it. Screen (below) answers them as the panel would.

Run as a program, this is the box's first step:  fbdev.py FD COMMAND...  installs the filter, passes its
listening end to the emulator over the socket FD, and becomes COMMAND."""
import ctypes, os, platform, select, socket, struct, sys, threading

XRES, YRES, PAGES = 320, 375, 2
# as the PC's kernel numbers them: QEMU has turned the firmware's requests into these before it asks
GET_VAR, PUT_VAR, GET_FIX, PAN, BLANK, VSYNC = 0x4600, 0x4601, 0x4602, 0x4606, 0x4611, 0x40044620
CAUGHT = (GET_VAR, PUT_VAR, GET_FIX, PAN, BLANK, VSYNC)
SYS_SECCOMP, SYS_IOCTL, ARCH_X86_64 = 317, 16, 0xc000003e
SET_MODE_FILTER, NEW_LISTENER = 1, 8
ALLOW, NOTIFY = 0x7fff0000, 0x7fc00000
NOTIF_RECV, NOTIF_SEND = 0xc0502100, 0xc0182101
CONTINUE = 1  # the answer "not ours: let the kernel have it"


def _filter():
    """The BPF program: on this PC's architecture, an ioctl whose request is one of CAUGHT is handed
    over; everything else goes on as ever."""
    ins = [(0x20, 0, 0, 4), (0x15, 0, 0, ARCH_X86_64),      # load arch; not ours -> allow
           (0x20, 0, 0, 0), (0x15, 0, 0, SYS_IOCTL),        # load the call's number; not ioctl -> allow
           (0x20, 0, 0, 24)]                                # load the request (the second argument's low half)
    ins += [(0x15, 0, 0, c) for c in CAUGHT]
    allow = len(ins)
    ins += [(0x06, 0, 0, ALLOW), (0x06, 0, 0, NOTIFY)]
    out = b''
    for i, (code, jt, jf, k) in enumerate(ins):
        if code == 0x15:
            if k in CAUGHT: jt = allow + 1 - (i + 1)        # a caught request -> notify
            else: jf = allow - (i + 1)
        out += struct.pack('<HBBI', code, jt, jf, k)
    return out


class _Prog(ctypes.Structure):
    _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.c_char_p)]


def install():
    """Put the filter on this process and all it starts. Returns the listening file descriptor, or None
    where it cannot be done (another kind of processor, an old kernel): the box then runs as before."""
    if platform.machine() not in ('x86_64', 'AMD64'): return None
    libc = ctypes.CDLL(None, use_errno=True)
    code = _filter()
    prog = _Prog(len(code) // 8, code)
    fd = libc.syscall(SYS_SECCOMP, SET_MODE_FILTER, NEW_LISTENER, ctypes.byref(prog))
    return fd if fd >= 0 else None


class Screen:
    """Answers the framebuffer's ioctls for the programs of one box. on_pan(yoffset) is told which page
    the panel shows."""

    def __init__(self, listener, fb_path, on_pan=None, on_blank=None):
        self.listener, self.on_pan, self.on_blank = listener, on_pan, on_blank
        st = os.stat(fb_path)
        self.file = (st.st_dev, st.st_ino)
        self.yoffset = 0
        self.libc = ctypes.CDLL(None, use_errno=True)
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def stop(self):
        self._stop = True
        try: os.close(self.listener)
        except OSError: pass

    def _var(self):
        return struct.pack('<8I12I20I', XRES, YRES, XRES, YRES * PAGES, 0, self.yoffset, 32, 0,
                           16, 8, 0, 8, 8, 0, 0, 8, 0, 24, 8, 0, *([0] * 20))

    @staticmethod
    def _fix():
        return struct.pack('<16sQIIIIHHH2xI4xQIIH2H2x', b'jzfb', 0, XRES * YRES * PAGES * 4, 0, 0, 2, 0, 1, 0,
                           XRES * 4, 0, 0, 0, 0, 0, 0)

    def _serve(self):
        buf = ctypes.create_string_buffer(80)
        poll = select.poll()
        poll.register(self.listener, select.POLLIN)
        while not self._stop:
            try: events = poll.poll(500)
            except (OSError, ValueError): return
            if any(e & (select.POLLHUP | select.POLLNVAL | select.POLLERR) for _, e in events): return  # the box is gone
            if not events: continue
            ctypes.memset(buf, 0, 80)
            if self.libc.ioctl(self.listener, ctypes.c_ulong(NOTIF_RECV), buf) < 0: continue
            ident, pid, _, _, _, _, fd, request, arg = struct.unpack_from('<QIIiIQQQQ', buf.raw)
            try: error, flags = self._answer(pid, fd, request & 0xffffffff, arg), 0
            except LookupError: error, flags = 0, CONTINUE
            except OSError as e: error, flags = -(e.errno or 5), 0
            reply = struct.pack('<QqiI', ident, 0, error, flags)
            self.libc.ioctl(self.listener, ctypes.c_ulong(NOTIF_SEND), reply)  # (fails if the caller died meanwhile)

    def _answer(self, pid, fd, request, arg):
        """0, having answered as the panel; LookupError when the file asked is not the panel."""
        try: st = os.stat(f'/proc/{pid}/fd/{fd}')
        except OSError: raise LookupError
        if (st.st_dev, st.st_ino) != self.file: raise LookupError
        if request in (GET_VAR, GET_FIX):
            self._write(pid, arg, self._var() if request == GET_VAR else self._fix())
        elif request in (PUT_VAR, PAN):
            self.yoffset = struct.unpack('<I', self._read(pid, arg + 20, 4))[0] % (YRES * PAGES)
            if self.on_pan: self.on_pan(self.yoffset)
        elif request == BLANK and self.on_blank:
            self.on_blank(arg)
        return 0

    @staticmethod
    def _write(pid, address, data):
        fd = os.open(f'/proc/{pid}/mem', os.O_RDWR)
        try: os.pwrite(fd, data, address)
        finally: os.close(fd)

    @staticmethod
    def _read(pid, address, n):
        fd = os.open(f'/proc/{pid}/mem', os.O_RDONLY)
        try: return os.pread(fd, n, address)
        finally: os.close(fd)


if __name__ == '__main__':
    sock = socket.socket(fileno=int(sys.argv[1]))
    listener = None
    try: listener = install()
    except Exception as e: print('q2emu: no screen filter:', e, file=sys.stderr, flush=True)
    try:
        if listener is None: sock.send(b'-')
        else: socket.send_fds(sock, [b'+'], [listener])
    except OSError: pass
    sock.close()
    if listener is not None: os.close(listener)
    try:  # the box gets as many of the PC's cores as a Q2 has (q2emu.Q2_CORES): its programs count them
        cores = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, cores[-int(os.environ.get('Q2EMU_CORES', 2)):])
    except (OSError, ValueError): pass
    os.execvp(sys.argv[2], sys.argv[2:])
