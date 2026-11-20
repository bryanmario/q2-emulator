"""The window's buttons: drawn with Pillow (rounded, smooth, with icons) instead of Tk's stock widgets.

A Button is a Tk label showing one of a few pictures of itself (resting, under the mouse, pressed,
unavailable). Two shapes: a 'tile' with its icon over a short caption, and a 'pill' with its icon
beside a small heading and a line of text. Everything is drawn at three times its size and scaled
down, which is what makes the edges and icons smooth."""
import math
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

# The window's colours: a dark blue. Change them here and the whole window follows.
BG = '#161d2b'            # the window, and what shows around the player
FACE = '#25304a'          # a button
FACE_HOVER = '#314062'
FACE_PRESSED = '#1d2740'
FACE_OFF = '#1b2336'      # an unavailable button
MENU = '#1e2740'          # the More menu
PANEL = '#1e2740'         # a panel over the player: the menu, a question, a note, the help
ACCENT = '#3767c9'        # the button that goes ahead
ACCENT_HOVER = '#4478de'
TIP = '#0e131e'           # a tooltip
TIP_EDGE = '#3a4868'
INK = '#eef2fa'           # icons and main text
DIM = '#94a3c2'           # headings, captions at rest
OFF = '#55627f'           # an unavailable button's icon and text
GREEN = '#63d18b'         # the player is on
RED = '#c8372d'           # recording
RED_HOVER = '#d9483e'
SS = 3                    # supersampling


def _font_file(name, fallback):
    try:
        out = subprocess.run(['fc-match', '-f', '%{file}', name], capture_output=True, text=True, timeout=5).stdout.strip()
        if out and os.path.isfile(out) and '[' not in out:  # variable fonts are left alone: plain files only
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return fallback


_REGULAR = _font_file('DejaVu Sans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
_BOLD = _font_file('DejaVu Sans:bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(_BOLD if bold else _REGULAR, size)
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


# -- Icons: each draws itself in a box of side s with its top-left corner at (x, y) --

def _stroke(d, points, colour, w):
    d.line(points, fill=colour, width=w, joint='curve')
    r = w / 2
    for px, py in (points[0], points[-1]):  # round ends
        d.ellipse((px - r, py - r, px + r, py + r), fill=colour)


def _arc(d, cx, cy, r, start, end, colour, w):
    d.arc((cx - r, cy - r, cx + r, cy + r), start, end, fill=colour, width=w)
    for a in (start, end):  # round ends
        px, py = cx + (r - w / 2) * math.cos(math.radians(a)), cy + (r - w / 2) * math.sin(math.radians(a))
        d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=colour)


def icon_power(d, x, y, s, c):
    w, cx, cy = round(s * 0.1), x + s / 2, y + s * 0.54
    _arc(d, cx, cy, s * 0.38, -55, 235, c, w)
    _stroke(d, [(cx, y + s * 0.1), (cx, y + s * 0.5)], c, w)


def icon_restart(d, x, y, s, c):
    w, cx, cy, r = round(s * 0.1), x + s / 2, y + s / 2, s * 0.38
    _arc(d, cx, cy, r, 40, 300, c, w)
    a = math.radians(300)
    px, py = cx + (r - w / 2) * math.cos(a), cy + (r - w / 2) * math.sin(a)
    t = s * 0.2  # the arrow head, pointing on round the circle
    d.polygon([(px + t * 0.95, py + t * 0.25), (px - t * 0.45, py - t * 0.95), (px - t * 0.35, py + t * 0.75)], fill=c)


def icon_camera(d, x, y, s, c):
    w = round(s * 0.09)
    d.rounded_rectangle((x + s * 0.08, y + s * 0.27, x + s * 0.92, y + s * 0.84), s * 0.13, outline=c, width=w)
    d.rounded_rectangle((x + s * 0.34, y + s * 0.14, x + s * 0.66, y + s * 0.32), s * 0.05, fill=c)
    r = s * 0.16
    d.ellipse((x + s / 2 - r, y + s * 0.555 - r, x + s / 2 + r, y + s * 0.555 + r), outline=c, width=w)


def icon_record(d, x, y, s, c):
    r = s * 0.3
    d.ellipse((x + s / 2 - r, y + s / 2 - r, x + s / 2 + r, y + s / 2 + r), fill=c)


def icon_stop(d, x, y, s, c):
    d.rounded_rectangle((x + s * 0.24, y + s * 0.24, x + s * 0.76, y + s * 0.76), s * 0.1, fill=c)


def icon_more(d, x, y, s, c):
    r = s * 0.085
    for k in (0.2, 0.5, 0.8):
        d.ellipse((x + s * k - r, y + s / 2 - r, x + s * k + r, y + s / 2 + r), fill=c)


def icon_folder(d, x, y, s, c):
    w = round(s * 0.09)
    d.rounded_rectangle((x + s * 0.1, y + s * 0.3, x + s * 0.9, y + s * 0.82), s * 0.1, outline=c, width=w)
    d.rounded_rectangle((x + s * 0.1, y + s * 0.18, x + s * 0.48, y + s * 0.4), s * 0.08, fill=c)


def icon_card(d, x, y, s, c):
    w = round(s * 0.09)
    outline = [(x + s * 0.22, y + s * 0.1), (x + s * 0.62, y + s * 0.1), (x + s * 0.8, y + s * 0.3),
               (x + s * 0.8, y + s * 0.9), (x + s * 0.22, y + s * 0.9), (x + s * 0.22, y + s * 0.1)]
    d.line(outline, fill=c, width=w, joint='curve')
    for k in (0.36, 0.5):  # the contacts
        _stroke(d, [(x + s * k, y + s * 0.24), (x + s * k, y + s * 0.4)], c, max(1, round(s * 0.07)))


ICONS = {'power': icon_power, 'restart': icon_restart, 'camera': icon_camera, 'record': icon_record,
         'stop': icon_stop, 'more': icon_more, 'folder': icon_folder, 'card': icon_card}


def _fit(draw, text, fnt, width):
    """text, shortened in its middle to fit width (a file's name keeps its start and its end)."""
    if draw.textlength(text, font=fnt) <= width:
        return text
    keep = len(text)
    while keep > 4:
        keep -= 1
        head = (keep + 1) // 2
        short = text[:head].rstrip() + '…' + text[len(text) - (keep - head):].lstrip()
        if draw.textlength(short, font=fnt) <= width:
            return short
    return text[:3] + '…'


def render(shape, size, icon, text, heading='', tone='', state='rest', enabled=True):
    """One picture of a button. tone: '' | 'on' (green icon) | 'rec' (red face).
    state: 'rest' | 'hover' | 'pressed'."""
    w, h = size
    W, H = w * SS, h * SS
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    if tone == 'rec':
        face = {'rest': RED, 'hover': RED_HOVER, 'pressed': '#a82c24'}[state]
    else:
        face = {'rest': FACE, 'hover': FACE_HOVER, 'pressed': FACE_PRESSED}[state] if enabled else FACE_OFF
    d.rounded_rectangle((0, 0, W - 1, H - 1), 13 * SS, fill=face)
    ink = INK if enabled else OFF
    icon_ink = '#ffffff' if tone == 'rec' else GREEN if tone == 'on' and enabled else ink
    if shape == 'plain':  # words only: a panel's own buttons. tone 'primary' is the one that goes ahead
        if enabled and tone == 'primary':
            face = {'rest': ACCENT, 'hover': ACCENT_HOVER, 'pressed': '#2a55a8'}[state]
        img.paste(PANEL, (0, 0, W, H))
        if enabled and tone != 'primary':  # lighter than the panel, with an edge: plainly a button
            face = {'rest': '#3a4a6e', 'hover': '#47598a', 'pressed': FACE_PRESSED}[state]
            d.rounded_rectangle((0, 0, W - 1, H - 1), 9 * SS, fill=face, outline='#7f8fb5', width=SS)
        else:
            d.rounded_rectangle((0, 0, W - 1, H - 1), 9 * SS, fill=face)
        f = font(11 * SS)
        d.text((W / 2, H / 2), _fit(d, text, f, W - 12 * SS), fill='#ffffff' if tone == 'primary' and enabled else ink,
               font=f, anchor='mm')
    elif shape == 'tile':
        s = 24 * SS
        ICONS[icon](d, (W - s) / 2, 9 * SS, s, icon_ink)
        f = font(10 * SS)
        caption_ink = '#ffffff' if tone == 'rec' else (ink if state != 'rest' or tone else DIM) if enabled else OFF
        d.text((W / 2, H - 13 * SS), _fit(d, text, f, W - 8 * SS), fill=caption_ink, font=f, anchor='mm')
    else:
        s = 22 * SS
        ICONS[icon](d, 12 * SS, (H - s) / 2, s, icon_ink)
        left, room = 43 * SS, W - 43 * SS - 10 * SS
        d.text((left, H / 2 - 9 * SS), heading, fill=DIM if enabled else OFF, font=font(9 * SS), anchor='lm')
        f = font(11 * SS)
        d.text((left, H / 2 + 8 * SS), _fit(d, text, f, room), fill=ink, font=f, anchor='lm')
    return img.resize((w, h), Image.LANCZOS)


def monitor(width, memory=None, work=None):
    """The monitor under the status line, as one picture, width wide.
    memory: (MB in use, the most it has been, MB a real Q2 has for its programs, MB a Q2 has in all,
    'what holds the most'). work: ('the last action's cost', 'the total'), in measuring mode."""
    height = (58 if memory else 0) + (40 if work else 0)
    W, H = width * SS, max(1, height) * SS
    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    y = 0
    if memory:
        used, peak, full, total, largest = memory
        over = used > full
        d.text((0, y + 8 * SS), 'MEMORY', fill=DIM, font=font(8 * SS, True), anchor='lm')
        d.text((W, y + 8 * SS), f'{round(used)} MB of {total} MB', fill=RED_HOVER if over else INK, font=font(10 * SS, True), anchor='rm')
        top, tall = y + 19 * SS, 10 * SS
        d.rounded_rectangle((0, top, W - 1, top + tall), tall // 2, fill=FACE)
        filled = int((W - 1) * min(used, total) / total)
        if filled > tall:
            d.rounded_rectangle((0, top, filled, top + tall), tall // 2, fill=RED if over else GREEN)
        if peak > used:   # the most it has been: a thin mark
            x = int((W - 1) * min(peak, total) / total)
            d.line((x, top, x, top + tall), fill=DIM, width=SS)
        x = int((W - 1) * full / total)   # where a real Q2 runs out
        d.line((x, top - 3 * SS, x, top + tall + 3 * SS), fill=INK, width=SS)
        d.text((0, y + 44 * SS), "Over a real Q2's limit. On the player this would likely fail." if over else largest,
               fill=RED_HOVER if over else DIM, font=font(9 * SS), anchor='lm')
        d.text((W, y + 44 * SS), f'Real Q2 limit {full} MB' + (f'   Peak {round(peak)} MB' if peak > used else ''),
               fill=DIM, font=font(9 * SS), anchor='rm')
        y += 58 * SS
    if work:
        last, all_of_it = work
        d.text((0, y + 8 * SS), 'PROCESSOR WORK', fill=DIM, font=font(8 * SS, True), anchor='lm')
        d.text((W, y + 8 * SS), f'Total {all_of_it}', fill=DIM, font=font(9 * SS), anchor='rm')
        d.text((0, y + 26 * SS), last, fill=INK, font=font(10 * SS), anchor='lm')
    return img.resize((width, max(1, height)), Image.LANCZOS)


class Button:
    """A drawn button in a Tk parent. set() changes what it shows; cget('text'/'state') reads it back."""

    def __init__(self, tk, ImageTk, parent, shape, icon, text, command, tip='', heading='', size=(80, 58), tone=''):
        self.tk, self.ImageTk = tk, ImageTk
        self.shape, self.icon, self.text, self.heading, self.tip = shape, icon, text, heading, tip
        self.command, self.size = command, size
        self.tone, self.enabled = tone, True
        self._state, self._inside, self._photos, self._tipwin, self._tipjob = 'rest', False, {}, None, None
        self.widget = tk.Label(parent, bd=0, highlightthickness=0, bg=PANEL if shape == 'plain' else BG, cursor='hand2')
        self.widget.bind('<Enter>', self._enter)
        self.widget.bind('<Leave>', self._leave)
        self.widget.bind('<ButtonPress-1>', self._press)
        self.widget.bind('<ButtonRelease-1>', self._release)
        self._show()

    def set(self, **changes):
        """icon, text, heading, tone, enabled, tip, size: only a real change redraws."""
        changed = False
        for key, value in changes.items():
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed = True
        if changed:
            self.widget.config(cursor='hand2' if self.enabled else 'arrow')
            self._show()

    config = set

    def cget(self, what):
        return {'text': self.text, 'state': 'normal' if self.enabled else 'disabled'}[what]

    def _show(self):
        state = self._state if self.enabled else 'rest'
        key = (self.size, self.icon, self.text, self.heading, self.tone, state, self.enabled)
        if key not in self._photos:
            if len(self._photos) > 40:  # a recording's timer makes a new picture every second
                self._photos.clear()
            self._photos[key] = self.ImageTk.PhotoImage(
                render(self.shape, self.size, self.icon, self.text, self.heading, self.tone, state, self.enabled))
        self.widget.config(image=self._photos[key])

    # -- mouse --

    def _enter(self, event):
        self._inside, self._state = True, 'hover'
        self._show()
        if self.tip:
            self._tipjob = self.widget.after(650, self._tip_show)

    def _leave(self, event):
        self._inside, self._state = False, 'rest'
        self._show()
        self._tip_hide()

    def _press(self, event):
        self._tip_hide()
        if self.enabled:
            self._state = 'pressed'
            self._show()

    def _release(self, event):
        was = self._state
        self._state = 'hover' if self._inside else 'rest'
        self._show()
        if was == 'pressed' and self._inside and self.enabled:
            self.command()

    # -- tooltip --

    def _tip_show(self):
        self._tipjob = None
        if self._tipwin or not self._inside:
            return
        # A label inside the window, not a window of its own: under WSL's display a small extra window
        # shows up late or in the wrong place, and takes the mouse from the button under it.
        top = self.widget.winfo_toplevel()
        self._tipwin = tip = self.tk.Label(top, text=self.tip, bg=TIP, fg=INK, font=('DejaVu Sans', 9), padx=8, pady=5,
                                           highlightthickness=1, highlightbackground=TIP_EDGE, justify='left',
                                           wraplength=max(120, top.winfo_width() - 40))
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = self.widget.winfo_rootx() - top.winfo_rootx() + (self.widget.winfo_width() - w) // 2
        y = self.widget.winfo_rooty() - top.winfo_rooty() - h - 6
        tip.place(x=max(4, min(x, top.winfo_width() - w - 4)), y=max(4, y))

    def _tip_hide(self):
        if self._tipjob:
            self.widget.after_cancel(self._tipjob)
            self._tipjob = None
        if self._tipwin:
            self._tipwin.destroy()
            self._tipwin = None


class Overlay:
    """One panel at a time, drawn inside the window over the player: the More menu, a question, a note,
    the help. Never a window of its own: under WSL's display those appear late or behind, keep hold of
    the mouse, and leave the main window looking frozen.

    open(where) gives an empty frame to fill with the helpers below. A click anywhere outside it closes
    it (the window calls outside() for every click)."""

    def __init__(self, tk, ImageTk, window, stage):
        self.tk, self.ImageTk, self.window, self.stage = tk, ImageTk, window, stage
        self.frame, self.kind, self.closed_at, self.closed_kind, self._job, self._on_close = None, None, 0, None, None, None
        self._keep = []

    def open(self, kind, where='center', seconds=None, on_close=None):
        """where: 'center', 'top' (a note) or 'bottom' (the menu, above the buttons)."""
        self.close()
        self.kind, self._on_close = kind, on_close
        self.frame = f = self.tk.Frame(self.window, bg=PANEL, highlightthickness=1, highlightbackground=TIP_EDGE,
                                       padx=6, pady=6)
        rely, anchor, y = {'center': (0.5, 'center', 0), 'top': (0.0, 'n', 10), 'bottom': (1.0, 's', -6)}[where]
        f.place(in_=self.stage, relx=0.5, rely=rely, anchor=anchor, y=y)
        f.lift()
        if seconds:
            self._job = self.window.after(int(seconds * 1000), self.close)
        return f

    def close(self):
        if self._job:
            self.window.after_cancel(self._job)
            self._job = None
        if self.frame:
            import time
            self.frame.destroy()
            self.frame, self._keep = None, []
            self.closed_at, self.closed_kind, self.kind = time.time(), self.kind, None
            done, self._on_close = self._on_close, None
            if done:
                done()

    @property
    def is_open(self):
        return self.frame is not None

    def outside(self, widget):
        """A click on widget: closes the panel unless the click is in it. True if it closed a panel that
        the click should not also act on (a menu or a question; a note just goes away)."""
        if not self.frame:
            return False
        w = widget
        while w is not None:
            if w is self.frame:
                return False
            w = getattr(w, 'master', None)
        swallow = self.kind != 'note'
        self.close()
        return swallow

    # -- what goes in a panel --

    def width(self):
        return max(260, min(400, self.window.winfo_width() - 40))

    def title(self, text):
        self.tk.Label(self.frame, text=text, bg=PANEL, fg=INK, font=('DejaVu Sans', 11, 'bold'), anchor='w',
                      justify='left', wraplength=self.width()).pack(fill='x', padx=8, pady=(4, 2))

    def text(self, text, dim=False, small=False):
        self.tk.Label(self.frame, text=text, bg=PANEL, fg=DIM if dim else INK, font=('DejaVu Sans', 9 if small else 10),
                      anchor='w', justify='left', wraplength=self.width()).pack(fill='x', padx=8, pady=2)

    def heading(self, text):
        self.tk.Label(self.frame, text=text.upper(), bg=PANEL, fg=DIM, font=('DejaVu Sans', 8, 'bold'), anchor='w'
                      ).pack(fill='x', padx=8, pady=(8, 1))

    def line(self):
        self.tk.Frame(self.frame, bg=TIP_EDGE, height=1).pack(fill='x', padx=4, pady=4)

    def pairs(self, rows):
        """Two columns: a short name and what it does."""
        grid = self.tk.Frame(self.frame, bg=PANEL)
        grid.pack(fill='x', padx=8)
        room = self.width() - 118
        for i, (name, what) in enumerate(rows):
            self.tk.Label(grid, text=name, bg=PANEL, fg=INK, font=('DejaVu Sans', 9, 'bold'), anchor='nw', width=14
                          ).grid(row=i, column=0, sticky='nw', pady=1)
            self.tk.Label(grid, text=what, bg=PANEL, fg=DIM, font=('DejaVu Sans', 9), anchor='w', justify='left',
                          wraplength=room).grid(row=i, column=1, sticky='w', pady=1)

    def _dot(self, on, bg):
        """The on/off mark of a row: a ring, filled in its middle when on."""
        size = 18
        big = Image.new('RGB', (size * SS, size * SS), bg)
        d = ImageDraw.Draw(big)
        d.ellipse((SS, SS, (size - 1) * SS - 1, (size - 1) * SS - 1), outline=INK if on else DIM, width=2 * SS)
        if on:
            d.ellipse((5 * SS, 5 * SS, (size - 5) * SS - 1, (size - 5) * SS - 1), fill=GREEN)
        photo = self.ImageTk.PhotoImage(big.resize((size, size), Image.LANCZOS))
        self._keep.append(photo)
        return photo

    def row(self, text, command, checked=None, enabled=True, keep_open=False, danger=False):
        """A line to click. checked True/False shows an on/off dot and the word; keep_open leaves the panel
        up (a switch); danger turns the line red under the mouse (it removes something)."""
        box = self.tk.Frame(self.frame, bg=PANEL, cursor='hand2' if enabled else 'arrow')
        box.pack(fill='x')
        parts, mark, dots = [box], None, None
        if checked is not None:
            dots = (self._dot(checked, PANEL), self._dot(checked, FACE_HOVER))
            mark = self.tk.Label(box, image=dots[0], bg=PANEL, bd=0)
            mark.pack(side='left', padx=(10, 0))
            parts.append(mark)
        label = self.tk.Label(box, text=text, bg=PANEL, fg=INK if enabled else OFF, anchor='w',
                              font=('DejaVu Sans', 10), padx=10 if checked is None else 8, pady=5)
        label.pack(side='left', fill='x', expand=True)
        parts.append(label)
        label.checked = checked
        if checked is not None:
            word = self.tk.Label(box, text='On' if checked else 'Off', bg=PANEL, fg=GREEN if checked else DIM,
                                 font=('DejaVu Sans', 9, 'bold'), padx=10)
            word.pack(side='right')
            parts.append(word)
        if enabled:
            def click(event):
                if not keep_open:
                    self.close()
                command()

            def paint(over):
                bg = (RED if danger else FACE_HOVER) if over else PANEL
                for part in parts:
                    part.config(bg=bg)
                if mark:
                    mark.config(image=dots[1 if over else 0])
                label.config(fg='#ffffff' if over and danger else INK)

            def leave(event):  # only when the mouse has left the whole line, not gone from one part to the next
                under = box.winfo_containing(event.x_root, event.y_root)
                if under not in parts:
                    paint(False)
            for part in parts:
                part.bind('<Enter>', lambda e: paint(True))
                part.bind('<Leave>', leave)
                part.bind('<ButtonRelease-1>', click)
        return label

    def choices(self, caption, names, current, colours, command):
        """A caption and a row of round colour dots to pick one of names from."""
        box = self.tk.Frame(self.frame, bg=PANEL)
        box.pack(fill='x', padx=10, pady=4)
        self.tk.Label(box, text=caption, bg=PANEL, fg=INK, font=('DejaVu Sans', 10)).pack(side='left')
        for name in reversed(names):
            size = 26
            big = Image.new('RGB', (size * SS, size * SS), PANEL)
            d = ImageDraw.Draw(big)
            if name == current:
                d.ellipse((0, 0, size * SS - 1, size * SS - 1), fill=INK)
            d.ellipse((3 * SS, 3 * SS, (size - 3) * SS - 1, (size - 3) * SS - 1), fill=colours[name], outline=PANEL, width=SS)
            photo = self.ImageTk.PhotoImage(big.resize((size, size), Image.LANCZOS))
            self._keep.append(photo)
            dot = self.tk.Label(box, image=photo, bg=PANEL, bd=0, cursor='hand2')
            dot.pack(side='right', padx=3)
            dot.bind('<ButtonRelease-1>', lambda e, n=name: (self.close(), command(n)))

    def buttons(self, *buttons):
        """(text, command, primary) from left to right, at the panel's foot. Each closes the panel."""
        bar = self.tk.Frame(self.frame, bg=PANEL)
        bar.pack(fill='x', padx=8, pady=(8, 4))
        made = []
        for text, command, primary in reversed(buttons):
            def go(command=command):
                self.close()
                if command:
                    command()
            b = Button(self.tk, self.ImageTk, bar, 'plain', None, text, go, size=(max(96, 26 + 9 * len(text)), 36),
                       tone='primary' if primary else '')
            b.widget.pack(side='right', padx=(6, 0))
            made.append(b)
        self._keep.append(made)
        return made