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
