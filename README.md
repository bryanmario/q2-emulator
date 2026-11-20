# Q2 Emulator

<p align="center">
  <img src="docs/demo.gif" width="280" alt="Q2 Emulator running a firmware: the menus, scanning the SD card, Cover Flow, Now Playing">
</p>

Q2 Emulator is a QEMU-based development tool for the Shanling Q2, built to run real firmware builds and test your changes directly on your computer.

**More time building, less time flashing.**

For developers: Boot the actual firmware in a virtual environment, complete with simulated display output and hardware input mapping. Inspect UI layout changes, verify screen transitions, and debug on-device navigation logic directly from your desktop. Validate each revision in QEMU and save time by skipping repeated hardware flashes until your build is ready for the player. Windows-only for now.

## Get started

1. Download **[Q2Emulator.exe](https://github.com/bryanmario/q2-emulator/releases/latest)** and put it in a folder of its own.
2. Double-click it. The first start sets itself up; after that it opens in a second or two.
3. Click **Firmware** and open your `update.tar` (or a firmware ZIP). No firmware is included.

It needs Windows 10 or 11 with WSL 2, and offers to turn that on if it is missing. Windows may warn about an unknown publisher: choose *More info*, then *Run anyway*.

<p align="center">
  <img src="docs/home.png" width="150" alt="Home">
  <img src="docs/cover-flow.png" width="150" alt="Cover Flow">
  <img src="docs/folder.png" width="150" alt="Browsing the SD card">
  <img src="docs/streaming.png" width="150" alt="Streaming">
  <img src="docs/eq.png" width="150" alt="Equalizer">
</p>

## What you get

- **The real firmware, on screen.** Touch it with the mouse, turn the wheel, press its buttons, or use the keyboard.
- **Your music as the SD card.** Pick any folder; the player browses it as its card.
- **Screenshots and video** of the screen or the whole player (F12, F9).
- **Six shell colors**, and a window you can size as you like.
- **AI agent control**, so a coding agent can drive the player and see the result. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Not there: sound, Wi-Fi and Bluetooth. A song opens in Now Playing but does not advance.

Building from source, and how it works: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). License: GPL v3.

> **Disclaimer:** Q2 Emulator is an evolution of an early version of [lampsonlnguyen/q2-emulation](https://github.com/lampsonlnguyen/q2-emulation), built on the amazing work done by Lampson.
