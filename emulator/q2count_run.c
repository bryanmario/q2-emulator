/* q2count-run: what the kernel starts for each of the firmware's programs in the emulator's measuring
 * mode (binfmt_misc names it as the interpreter). It becomes the counting QEMU, /q2emu-host/qemu-mipsel,
 * with the counter loaded.
 *
 * It is here for one reason. The firmware's programs run with LD_PRELOAD naming the emulator's shim, a
 * MIPS library. The usual QEMU is one static file and never looks at that. The counting QEMU is not: the
 * PC's own loader starts it, would try to preload the MIPS library into QEMU itself, and say so on
 * stderr at every program start. So the variable is taken out of QEMU's own environment and handed to
 * the program QEMU runs (-E). Built static: it runs inside the firmware's root. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char *args[argc + 8], *preload = getenv("LD_PRELOAD");
    static char keep[4200];
    int n = 0;
    args[n++] = "qemu-mipsel";
    args[n++] = "-plugin";
    args[n++] = "/q2emu-host/libq2count.so";
    if (preload && *preload) {
        snprintf(keep, sizeof keep, "LD_PRELOAD=%s", preload);
        args[n++] = "-E";
        args[n++] = keep;
        unsetenv("LD_PRELOAD");
    }
    for (int i = 1; i < argc; i++) args[n++] = argv[i];
    args[n] = 0;
    execv("/q2emu-host/qemu-mipsel", args);
    perror("q2count-run: /q2emu-host/qemu-mipsel");
    return 127;
}
