#!/usr/bin/env python3
"""Draw packaging/icon.ico: the player (orange shell), small, for Q2Emulator.exe.  tools/make_icon.py"""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'emulator'))
from PIL import Image, ImageDraw
from simulator_device import DEVICE_SIZE, SCREEN_SIZE, device_frame

screen = Image.new('RGB', SCREEN_SIZE, '#0a090b')  # a few lines, as a list on the screen
d = ImageDraw.Draw(screen)
for i, w in enumerate((210, 150, 180, 120)):
    d.rounded_rectangle((40, 60 + i * 55, 40 + w, 84 + i * 55), 12, fill='#e8e8e8' if i == 0 else '#6a6a6a')
device = device_frame(screen, 'Orange').convert('RGBA')
mask = Image.new('L', DEVICE_SIZE)
ImageDraw.Draw(mask).rounded_rectangle((5, 1, 429, 659), 72, fill=255)
device.putalpha(mask)
square = Image.new('RGBA', (DEVICE_SIZE[1], DEVICE_SIZE[1]), (0, 0, 0, 0))
square.paste(device, ((DEVICE_SIZE[1] - DEVICE_SIZE[0]) // 2, 0))
out = ROOT/'packaging/icon.ico'
square.resize((256, 256), Image.LANCZOS).save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
square.resize((256, 256), Image.LANCZOS).save(ROOT/'emulator/icon.png')  # the window's own (title bar, taskbar)
print(out)
