# Working on Q2 Emulator

The emulator is a Linux program. The Windows download wraps it: `Q2Emulator.exe` keeps a small private
Linux (a WSL 2 distribution named `Q2Emulator`) and runs the emulator inside it.

## From source

    ./start.sh                      # open the window, then "Firmware"
    ./start.sh path/to/update.tar   # open a firmware straight away
    tests/run_all.sh                # every check, no root needed

It needs Linux (WSL 2 works), root, Python 3 with Pillow and Tk, squashfs-tools, ffmpeg, and clang + lld
if you change `shim.c`. It also needs QEMU's `qemu-mipsel-static`, version 11.1.3, at
`bin/qemu-mipsel-static`: build it from [QEMU's source](https://www.qemu.org/download/#source)
(the version Ubuntu 24.04 ships, 8.2.2, deadlocks under the firmware). It is not kept in git.

## What is where

- `emulator/q2emu.py` the engine: unpacks the firmware and runs it under QEMU in a sandbox
- `emulator/shim.c` stands in for the Q2's hardware (`libq2emu.so` is it compiled ahead, by `tools/build_shim.py`)
- `emulator/emulator_gui.py` the window; `emulator/controls.py` its buttons and panels;
  `emulator/device_view.py` the player drawn at the window's size; `emulator/simulator_device.py` the player's drawing
- `emulator/fbdev.py` answers the screen's ioctls for programs the shim cannot be loaded into (statically
  linked ones, such as Q2 Pod's App Store apps), through a seccomp filter on the sandbox
- `emulator/wifi.py` the pretend Wi-Fi: it answers as `wpa_supplicant`, and `slirp4netns` gives the sandbox
  an interface named `wlan0` that leads out through the PC
- `emulator/q2count.c`, `emulator/q2count_run.c` measuring mode: a QEMU plugin that counts instructions, and
  the launcher that starts each program under it. `tools/build_count.sh` builds them, with a second QEMU
  (the same 11.1.3, source unchanged, built with plugins), into `bin/count/`
- `emulator/agent_door.py`, `emulator/agent_mcp.py` AI agent control (below)
- `packaging/` builds the Windows download (`build.ps1`): one file, `Q2Emulator.exe`, which unpacks a `data`
  folder beside itself. `Q2Emulator.exe --uninstall` removes it from a PC.
- `tests/` the checks; the firmware binaries some of them use are not in git (`tests/fixtures/README.md`)

## AI agent control

A program can drive the emulator: load a firmware, look at the screen, tap, press buttons, turn the wheel,
and get its steps back as a replayable script. It listens on this computer only, needs a token, and is off
unless asked for.

- **With a window, to watch the agent work:** More > "AI agent control".
- **Without a window:** the connector starts the emulator by itself.

The connector is an MCP server. Register it with your agent as:

    Windows download:  wsl.exe -d Q2Emulator -u root -e python3 /opt/q2-emulator/emulator/agent_mcp.py
    From source:       sudo python3 emulator/agent_mcp.py

Tools: `status`, `load_firmware`, `screenshot`, `tap`, `swipe`, `key`, `wheel`, `wait`, `power`, `restart`,
`steps`, `firmware_log`. The same control speaks plain HTTP for scripts: see the top of `emulator/agent_door.py`.

## What the emulator changes, and what it leaves alone

It works on a copy of the firmware unpacked on the PC; the firmware file is never written to. In that copy
it stands in for hardware the PC does not have (the screen, the keys, the wheel, the battery, the SD card
slot). The music player's own authorization is left as it is, so a song opens but does not advance.

In that copy, and only there:

- `demo`'s check of the player's serial number length is made to pass, so its input handlers are set up.
- These programs are replaced by scripts of the same name (the originals are kept beside them as
  `NAME.q2emu-orig`): the watchdog `checkappprocess.sh`; `rtk_hciattach` and `hciconfig` (without them the
  firmware shows "Bluetooth abnormal" and restarts once Bluetooth is on); and, while the emulator's internet
  is on, `wpa_supplicant` and `udhcpc`.
- `/proc/meminfo` says what a Q2 would have left, and the sandbox is kept to two of the PC's cores.

## Measured on a real Q2

The figures the emulator uses come from a Shanling Q2 running Q2 Pod 1.8.8 (November 2026):

| | real Q2 | in the emulator |
|---|---|---|
| Memory | 117,864 kB after its kernel; about 83 MB available when idle | the same total is reported; the meter turns red at 111 MB |
| Cores | 2 | the sandbox is kept to 2 |
| Touch and wheel | a report every 12 ms; a tap about 60 ms; the wheel 200 units a circle | the same |
| Buttons | a press about 125 ms | the same for the keyboard; a mouse click lasts as long as you hold it |
| Whole-number work | 1.0 to 1.3 G instructions a second | about 2 times faster (one PC) |
| Decimal work | 0.31 G instructions a second | about 3 times slower |
| Scattered memory work | 6 million reads in 1.4 s | about 29 times faster |
| SD card | 21 MB a second | as fast as the PC's disk |
| Screen | 320x375, 60 Hz, two pages; a plain write to `/dev/fb0` shows nothing | a plain write does show: the screen is a file here |

Measuring mode's "at least N s of a Q2's processor" uses the whole-number rate, so it is a floor.
`/status` (AI agent control) gives `memory` (used, peak, whether over a Q2's) and `work_instructions`.
