/* libq2emu.so: preloaded into the Q2's own programs under qemu-mipsel, it answers the ioctls the Q2's
 * hardware would, for the plain files and sockets that stand in for the device nodes (q2emu.py makes them),
 * and logs every ioctl it sees to $Q2EMU_DIR/ioctl.log, so a new hardware request is found, not guessed.
 * No libc headers: the build has only the firmware's own libraries to link against. */
typedef unsigned int u32;
typedef unsigned short u16;
int *__errno_location(void);
char *getenv(const char *);

extern void *stdout;
int setvbuf(void *, char *, int, unsigned);
/* demo's stdout goes to a log file here, block-buffered: unbuffered, so its last lines survive a crash. */
__attribute__((constructor)) static void unbuffered(void) { setvbuf(stdout, 0, 2 /* _IONBF */, 0); }

static long sc(long n, long a, long b, long c) {
    register long v0 __asm__("$2") = n, a0 __asm__("$4") = a, a1 __asm__("$5") = b, a2 __asm__("$6") = c;
    register long a3 __asm__("$7");
    __asm__ volatile("syscall" : "+r"(v0), "=r"(a3) : "r"(a0), "r"(a1), "r"(a2)
                     : "$1", "$3", "$8", "$9", "$10", "$11", "$12", "$13", "$14", "$15", "$24", "$25", "hi", "lo", "memory");
    return a3 ? -v0 : v0;
}
enum { SYS_write = 4004, SYS_open = 4005, SYS_close = 4006, SYS_ioctl = 4054, SYS_readlink = 4085,
       SYS_connect = 4170, SYS_getpeername = 4171, SYS_socket = 4183 };
enum { XRES = 320, YRES = 375, PAGES = 2 }; /* the panel's portrait frame, 32 bpp, two pages to pan between */

struct bitfield { u32 offset, length, msb_right; };
struct var {
    u32 xres, yres, xres_virtual, yres_virtual, xoffset, yoffset, bits_per_pixel, grayscale;
    struct bitfield red, green, blue, transp;
    u32 nonstd, activate, height, width, accel_flags, pixclock, left_margin, right_margin, upper_margin,
        lower_margin, hsync_len, vsync_len, sync, vmode, rotate, colorspace, reserved[4];
};
struct fix {
    char id[16];
    unsigned long smem_start;
    u32 smem_len, type, type_aux, visual;
    u16 xpanstep, ypanstep, ywrapstep;
    u32 line_length;
    unsigned long mmio_start;
    u32 mmio_len, accel;
    u16 capabilities, reserved[2];
};
static struct var var = { XRES, YRES, XRES, YRES * PAGES, 0, 0, 32, 0, { 16, 8, 0 }, { 8, 8, 0 }, { 0, 8, 0 },
                          { 24, 8, 0 } };

static unsigned len(const char *s) { unsigned n = 0; while (s[n]) n++; return n; }
static int ends(const char *s, unsigned n, const char *tail) {
    unsigned t = len(tail);
    if (n < t) return 0;
    for (unsigned i = 0; i < t; i++) if (s[n - t + i] != tail[i]) return 0;
    return 1;
}
static void zero(void *p, unsigned n) { for (unsigned i = 0; i < n; i++) ((char *)p)[i] = 0; }
static char *put(char *o, const char *s) { while (*s) *o++ = *s++; return o; }
static char *hex(char *o, unsigned long v) {
    for (int i = 28; i >= 0; i -= 4) *o++ = "0123456789abcdef"[(v >> i) & 15];
    return o;
}
static char *dir_path(char *o, const char *name) {
    const char *d = getenv("Q2EMU_DIR");
    o = put(o, d ? d : "/tmp/q2emu");
    o = put(o, name);
    *o = 0;
    return o;
}
static void note(const char *path, unsigned long req, long ret, const char *how) {
    char file[256], line[512], *o = line;
    dir_path(file, "/ioctl.log");
    int fd = sc(SYS_open, (long)file, 0x109 /* O_WRONLY | O_APPEND | O_CREAT, MIPS */, 0644);
    if (fd < 0) return;
    o = put(o, path), o = put(o, " 0x"), o = hex(o, req), o = put(o, " = 0x"), o = hex(o, ret);
    o = put(o, " "), o = put(o, how), *o++ = '\n';
    sc(SYS_write, fd, (long)line, o - line);
    sc(SYS_close, fd, 0, 0);
}
static void tell(const char *name, unsigned long value) { /* a number in a file of $Q2EMU_DIR, for the viewer */
    char file[256], v[9];
    dir_path(file, name);
    int fd = sc(SYS_open, (long)file, 0x301 /* O_WRONLY | O_CREAT | O_TRUNC, MIPS */, 0644);
    if (fd < 0) return;
    hex(v, value), v[8] = '\n';
    sc(SYS_write, fd, (long)v, 9);
    sc(SYS_close, fd, 0, 0);
}
static void pan(void) { tell("/fb.pan", var.yoffset); } /* the page the panel shows */

/* An input device node is opened as a connection to the launcher's socket of its name
 * ($Q2EMU_DIR/eventN.sock): as with evdev, every opener then gets every event, where a FIFO would deal
 * each event to one of its readers (demo opens the wheel twice, and the keys from more than one place). */
struct sockaddr_un { u16 family; char path[108]; };
static int input_node(const char *path) { /* N of /dev/input/eventN, or -1 */
    const char *want = "/dev/input/event";
    unsigned i = 0;
    for (; want[i]; i++) if (path[i] != want[i]) return -1;
    return path[i] >= '0' && path[i] <= '9' && !path[i + 1] ? path[i] - '0' : -1;
}
static long input_open(int n) {
    struct sockaddr_un a;
    zero(&a, sizeof a);
    a.family = 1; /* AF_UNIX */
    char *o = dir_path(a.path, "/event");
    *o++ = '0' + n, o = put(o, ".sock"), *o = 0;
    long fd = sc(SYS_socket, 1, 2 /* SOCK_STREAM, MIPS */ | 02000000 /* O_CLOEXEC */, 0);
    if (fd < 0) return fd;
    long r = sc(SYS_connect, fd, (long)&a, sizeof a);
    if (r < 0) return sc(SYS_close, fd, 0, 0), r;
    return fd;
}
int open(const char *path, int flags, int mode) {
    int n = input_node(path);
    long fd = n >= 0 ? input_open(n) : sc(SYS_open, (long)path, flags | 0x2000 /* O_LARGEFILE, MIPS */, mode);
    if (fd < 0) return *__errno_location() = -fd, -1;
    return fd;
}
int open64(const char *path, int flags, int mode) { return open(path, flags, mode); }

/* A folder's entries. demo lists folders with the 32-bit readdir(), which fails (EOVERFLOW) on an entry
 * whose inode number or place in the folder needs more than 32 bits: never on the Q2's FAT card, always
 * on a PC's ext4 or a Windows folder, so every card looked empty. It is answered from the 64-bit one.
 * The 32-bit entry is the 64-bit one without its first eight bytes, once the two short numbers are put
 * where the long place was: the length, type and name already sit where they should. */
struct dirent64 { unsigned long long ino; long long off; u16 reclen; unsigned char type; char name[256]; };
struct dirent32 { u32 ino; int off; u16 reclen; unsigned char type; char name[256]; };
struct dirent64 *readdir64(void *);
struct dirent32 *readdir(void *dir) {
    struct dirent64 *e = readdir64(dir);
    if (!e) return 0;
    u32 ino = (u32)e->ino ^ (u32)(e->ino >> 32), off = (u32)e->off & 0x7fffffff;
    struct dirent32 *out = (struct dirent32 *)&e->off;
    out->ino = ino ? ino : 1, out->off = off; /* 0 would read as a deleted entry */
    return out;
}

/* evdev: what the Q2's keys, touch panel and wheel answer. As the kernel, it writes no more than the
 * answer's own length, whatever length the request names: tslib asks for EV's bits with eight times its
 * buffer's size (sizeof(bit) * 8), and the kernel's 4 bytes are all its buffer has room for. */
static long evdev(const char *path, unsigned n, unsigned long req, unsigned char *arg) {
    unsigned nr = req & 0xff, size = (req >> 16) & 0x1fff, have = 0;
    int wheel = ends(path, n, "event3"), touch = ends(path, n, "event2");
    unsigned char b[96];
    if (((req >> 8) & 0xff) != 'E') return -25;
    if (!(req & 0x40000000)) return 0; /* a write (EVIOCGRAB and the like): accepted */
    zero(b, sizeof b);
    if (nr == 0x01) return *(int *)arg = 0x010001, 0;           /* EVIOCGVERSION */
    if (nr == 0x06) {                                              /* EVIOCGNAME */
        const char *name = wheel ? "hyn_sliderbar" : touch ? "hyn_ts" : "md-gpio-keys";
        have = len(name) + 1;
        for (unsigned i = 0; i < have; i++) b[i] = name[i];
    } else if (nr == 0x20) b[0] = wheel || touch ? 0x0b : 0x03, have = 4; /* EV_SYN, EV_KEY (, EV_ABS) */
    else if (nr == 0x21) {                                         /* EV_KEY's bits: every key and button */
        for (have = 0; have < 96; have++) b[have] = 0xff;
    } else if (nr == 0x23) {                                       /* EV_ABS's: X, Y and PRESSURE (0x18) */
        if (wheel || touch) b[0] = 0x03, b[3] = 0x01;
        have = 8;
    } else if (nr >= 0x40 && nr < 0x80) {                          /* EVIOCGABS: value, min, max, fuzz, flat, res */
        int axis = nr - 0x40, max = wheel ? 200 : axis == 0 ? XRES - 1 : axis == 1 ? YRES - 1 : 1;
        for (unsigned i = 0; i < 4; i++) b[8 + i] = max >> (8 * i);
        have = 24;
    } else if (nr == 0x02) have = 8;                               /* EVIOCGID */
    else if (nr == 0x09) have = 4;                                 /* EVIOCGPROP */
    else if (nr == 0x18) have = 96;                                /* EVIOCGKEY: nothing held */
    else if (nr > 0x20 && nr < 0x40) have = 2;                     /* the other event types' bits: none */
    else have = size < 8 ? size : 8;
    if (have > size) have = size;
    for (unsigned i = 0; i < have; i++) arg[i] = b[i];
    return nr >= 0x40 && nr < 0x80 ? 0 : have;
}

/* g_backlight_status (1 byte, .bss): the firmware ignores all input when this is 0. On real hardware the
   MCU sets it; here it is forced on at every MCU ioctl. The launcher looks its address up in demo's symbols
   and passes it as $Q2EMU_BL_ADDR (hex), so another firmware build is not written into at a stale address.
   Only demo itself is written into: its children (hciplayer, ...) inherit LD_PRELOAD and the variable, but
   the address means nothing in them. */
static int in_demo(void) { /* is this process demo itself? */
    static int known;
    if (!known) {
        char exe[256];
        long n = sc(SYS_readlink, (long)"/proc/self/exe", (long)exe, sizeof exe - 1);
        known = n >= 0 && ends(exe, n, "/release/bin/demo") ? 1 : -1;
    }
    return known > 0;
}
static volatile unsigned char *backlight(void) {
    static int known;
    static unsigned long addr;
    if (known) return (volatile unsigned char *)addr;
    known = 1;
    if (!in_demo()) return 0;
    const char *s = getenv("Q2EMU_BL_ADDR");
    if (!s) return 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    unsigned long a = 0;
    for (; *s; s++) {
        unsigned v = *s >= '0' && *s <= '9' ? *s - '0' : *s >= 'a' && *s <= 'f' ? *s - 'a' + 10
                   : *s >= 'A' && *s <= 'F' ? *s - 'A' + 10 : 16;
        if (v > 15) return 0;
        a = a * 16 + v;
    }
    addr = a;
    return (volatile unsigned char *)addr;
}

int ioctl(int fd, unsigned long req, void *arg) {
    char link[32], path[256], *o = put(link, "/proc/self/fd/");
    unsigned f = fd, d = 1;
    while (f / d >= 10) d *= 10;
    for (; d; d /= 10) *o++ = '0' + f / d % 10;
    *o = 0;
    long n = sc(SYS_readlink, (long)link, (long)path, sizeof path - 1), ret;
    if (n < 0) n = 0;
    path[n] = 0;
    if (n > 7 && path[0] == 's' && path[6] == ':') { /* "socket:[N]": an input node's, by its peer's name */
        struct sockaddr_un a;
        int size = sizeof a;
        zero(&a, sizeof a);
        if (sc(SYS_getpeername, fd, (long)&a, (long)&size) >= 0 && ends(a.path, len(a.path), ".sock")) {
            unsigned l = len(a.path);
            o = put(path, "/dev/input/event"), *o++ = a.path[l - 6], *o = 0;
            n = o - path;
        }
    }
    const char *how = "kernel";
    if (ends(path, n, "/dev/fb0")) {
        how = "fb", ret = 0;
        if (req == 0x4600) *(struct var *)arg = var;                   /* FBIOGET_VSCREENINFO */
        else if (req == 0x4601 || req == 0x4606) {                     /* FBIOPUT_VSCREENINFO, FBIOPAN_DISPLAY */
            var.yoffset = ((struct var *)arg)->yoffset % (YRES * PAGES);
            pan();
            static int drawn; /* demo's first picture: the boot's loading screen is over */
            if (!drawn && in_demo()) drawn = 1, tell("/fb.drawn", 1);
        } else if (req == 0x4602) {                                    /* FBIOGET_FSCREENINFO */
            struct fix *x = arg;
            zero(x, sizeof *x);
            put(x->id, "jzfb");
            x->smem_len = XRES * YRES * PAGES * 4, x->visual = 2 /* TRUECOLOR */, x->ypanstep = 1;
            x->line_length = XRES * 4;
        } else if (req == 0x4611) {                                    /* FBIOBLANK: 0 switches the panel on, */
            tell("/fb.blank", (unsigned long)arg);                     /* anything else off (the idle timer) */
        } else how = "fb?"; /* vsync and the panel's own: accepted */
    } else if (n > 6 && ends(path, n - 1, "/dev/input/event")) {
        how = "evdev", ret = evdev(path, n, req, arg);
    } else if (ends(path, n, "/dev/mcu")) {
        how = "mcu"; ret = 0;
        volatile unsigned char *bl = backlight();
        if (bl && !*bl) *bl = 1;
        /* MCU read ioctls: direction bit 31 set, type 'M' (0x4d).  Fill the arg buffer so the firmware
           sees sane emulator defaults instead of uninitialized stack values (zeros = backlight off). */
        if ((req & 0x80000000) && arg) {
            unsigned size = (req >> 16) & 0x1fff;
            if (size >= 4) *(u32 *)arg = ((req & 0xff) == 0x73) ? 0x01 : 0;
            else { unsigned char *p = arg; for (unsigned i = 0; i < size; i++) p[i] = 0; }
        }
    } else {
        ret = sc(SYS_ioctl, fd, req, (long)arg);
        if (ret < 0 && ends(path, n, "/dev/shanling_dac")) how = "fake?", ret = 0;
    }
    note(path, req, ret, how);
    if (ret < 0) return *__errno_location() = -ret, -1;
    return ret;
}
