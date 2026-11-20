/* libq2count.so: a QEMU plugin that counts the instructions the firmware's programs execute.
 *
 * How long something takes here says little about a Q2: QEMU runs as fast as the PC allows, quicker than
 * a Q2 at some things and slower at others. How many of the Q2's instructions it took is the same on any
 * PC, and the same on a Q2. So in the emulator's measuring mode every program of the box runs under a QEMU
 * built with plugins (tools/build_count.sh; QEMU's source unchanged) with this loaded, and each process
 * keeps its count in a file of its own, $Q2EMU_DIR/work/PID.N (8 bytes, the count so far). q2emu adds
 * the files up.
 *
 * A block of translated code adds its instructions when it is entered, so a block left early (a fault,
 * a signal) counts whole: a little over, never under. The file is brought up to date at the process's
 * system calls, at most every 50 ms, and at its end. A forked child starts its own file from zero. */
#include <fcntl.h>
#include <glib.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

static qemu_plugin_u64 count;
static int fd = -1, owner;
static uint64_t base, shown_at;

static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC_COARSE, &t);
    return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static void open_file(void) { /* a file of this process's own: after a fork, the child's */
    const char *dir = getenv("Q2EMU_DIR");
    char path[300];
    if (fd >= 0) close(fd);
    owner = getpid();
    for (int n = 0; n < 64; n++) {
        snprintf(path, sizeof path, "%s/work/%d.%d", dir ? dir : "/tmp/q2emu", owner, n);
        fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (fd >= 0) return;
    }
}

static void publish(void) {
    uint64_t all = qemu_plugin_u64_sum(count);
    if (getpid() != owner) base = all, open_file(); /* a forked child: what its parent ran is the parent's */
    uint64_t mine = all - base;
    if (fd >= 0 && pwrite(fd, &mine, sizeof mine, 0) < 0) { }
}

static void on_tb(struct qemu_plugin_tb *tb, void *p) {
    qemu_plugin_register_vcpu_tb_exec_inline_per_vcpu(tb, QEMU_PLUGIN_INLINE_ADD_U64, count,
                                                      qemu_plugin_tb_n_insns(tb));
}

static void on_syscall(unsigned int vcpu, int64_t num, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                       uint64_t a5, uint64_t a6, uint64_t a7, uint64_t a8, void *p) {
    uint64_t t = now_ms();
    if (t - shown_at >= 50 || getpid() != owner) shown_at = t, publish();
}

static void on_exit_(void *p) { publish(); }

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info, int argc, char **argv) {
    count = qemu_plugin_scoreboard_u64(qemu_plugin_scoreboard_new(sizeof(uint64_t)));
    open_file();
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb, NULL);
    qemu_plugin_register_vcpu_syscall_cb(id, on_syscall, NULL);
    qemu_plugin_register_atexit_cb(id, on_exit_, NULL);
    return 0;
}
