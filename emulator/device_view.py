"""The player as the window shows it: at any size, with smooth edges.

simulator_device draws the player at one size (435x664), which is what screenshots and videos keep.
For the window the same drawing is made here at the size asked for, several times larger and scaled
down (that is what smooths its curves), and kept. Only the screen changes from one frame to the next,
so the window holds two pictures: the player, redrawn when its size or colour changes, and the screen
on top of it, redrawn with every new frame."""
from PIL import Image, ImageColor, ImageDraw

from simulator_device import (
    CENTRE, DEVICE_SIZE, RADIUS, SCREEN_AT, SCREEN_SIZE, SHELL_FINISHES, WHEEL,
)

OUTLINE = (5, 1, 429, 659, 72)       # the player's outer edge: a rounded rectangle and its corner radius
SCREEN_CORNER = 41


def _draw_shell(shell, k):
    """simulator_device.enclosure's drawing, k times the size. Returns (picture, mask of the player)."""
    finish = SHELL_FINISHES[shell]
    W, H = round(DEVICE_SIZE[0] * k), round(DEVICE_SIZE[1] * k)

    def box(x0, y0, x1, y1):  # Pillow's boxes include their last pixel
        return (round(x0 * k), round(y0 * k), round((x1 + 1) * k) - 1, round((y1 + 1) * k) - 1)

    def w(width):
        return max(1, round(width * k))

    def p(*xy):
        return tuple(v * k for v in xy)

    frame = Image.new('RGB', (W, H), '#201a1a')
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle(box(5, 1, 429, 659), 72 * k, '#201a1a', outline='#120e0f', width=w(2))
    top, bottom = ImageColor.getrgb(finish.top), ImageColor.getrgb(finish.bottom)
    column = Image.new('RGB', (1, 256))
    column.putdata([tuple(round(a + (b - a) * i / 255) for a, b in zip(top, bottom)) for i in range(256)])
    body = column.resize((W, H), Image.BILINEAR)
    mask = Image.new('L', (W, H))
    ImageDraw.Draw(mask).rounded_rectangle(box(10, 6, 424, 654), 68 * k, fill=255)
    frame.paste(body, mask=mask)
    draw.rounded_rectangle(box(11, 7, 423, 653), 67 * k, outline=finish.trim, width=w(2))
    draw.rounded_rectangle(box(17, 15, 417, 359), 64 * k, '#151417', outline='#392222', width=w(2))
    draw.rounded_rectangle(box(22, 20, 412, 354), 60 * k, '#0a090b', outline='#2e292b', width=w(2))
    x, y = WHEEL
    draw.ellipse(p(x - RADIUS - 3, y - RADIUS - 2, x + RADIUS + 3, y + RADIUS + 4), finish.wheel_shadow)
    draw.ellipse(p(x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS), finish.wheel, outline=finish.wheel_edge, width=w(2))
    draw.ellipse(p(x - CENTRE, y - CENTRE, x + CENTRE, y + CENTRE), finish.center, outline=finish.center_edge, width=w(3))
    draw.arc(p(x - CENTRE + 3, y - CENTRE + 3, x + CENTRE - 3, y + CENTRE - 3), 190, 318, finish.center_glint, width=w(2))
    ink, line = finish.wheel_ink, w(3)

    def stroke(*xy):  # a line with round ends and corners
        pts = [p(*xy[i:i + 2]) for i in range(0, len(xy), 2)]
        draw.line(pts, fill=ink, width=line, joint='curve')
        r = line / 2
        for px, py in (pts[0], pts[-1]):
            draw.ellipse((px - r, py - r, px + r, py + r), fill=ink)

    draw.arc(p(x - 12, y - 95, x + 11, y - 76), -90, 125, fill=ink, width=line)   # back
    stroke(x - 1, y - 98, x - 8, y - 95, x - 1, y - 90)
    for sign in (-1, 1):                                                          # previous, next
        mid = x + 85 * sign
        for shift in (-4, 4):
            stroke(mid + shift - 3 * sign, y - 6, mid + shift + 2 * sign, y, mid + shift - 3 * sign, y + 6)
    draw.polygon([p(x - 11, y + 78), p(x - 11, y + 92), p(x, y + 85)], fill=ink)  # play / pause
    for bar in (x + 4, x + 10):
        stroke(bar, y + 78, bar, y + 92)
    outline = Image.new('L', (W, H))
    ImageDraw.Draw(outline).rounded_rectangle(box(*OUTLINE[:4]), OUTLINE[4] * k, fill=255)
    return frame, outline


def _sprite(kind, radius):
    """A touch ring or a lit button, drawn large and scaled down: (picture with its own transparency)."""
    r = max(2, round(radius))
    big = Image.new('RGBA', (r * 8, r * 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    if kind == 'touch':
        d.ellipse((0, 0, r * 8 - 1, r * 8 - 1), fill=(255, 255, 255, 70), outline=(255, 255, 255, 210), width=max(4, r // 2))
    else:
        d.ellipse((0, 0, r * 8 - 1, r * 8 - 1), fill=(255, 255, 255, 70))
    return big.resize((r * 2, r * 2), Image.LANCZOS)


class DeviceView:
    """Pictures of the player for one window. size is the player's (width, height) on screen."""

    def __init__(self, backdrop):
        self.backdrop = backdrop
        self._fine = {}      # (size, shell) -> the player, drawn with care
        self._rough = None   # (size, shell, picture): a quick one, while the window is being dragged
        self._masks = {}     # size -> the screen's rounded-corner mask
        self._base = (None, None, None, None, None)  # (size, id, shell, the player last drawn, its screen's part)
        self._sprites = {}

    @staticmethod
    def fit(width, height):
        """The player's size in a space of width x height, and its scale against the drawing's own."""
        s = max(0.3, min(width / DEVICE_SIZE[0], height / DEVICE_SIZE[1]))
        for whole in (1, 2, 3):  # with room for only a little more than a whole size, keep the whole size:
            if whole <= s < whole * 1.06:  # there the screen is the firmware's own pixels, not a blend of them
                s = whole
        return (round(DEVICE_SIZE[0] * s), round(DEVICE_SIZE[1] * s)), s

    def _on_backdrop(self, frame, outline, size):
        back = Image.new('RGB', frame.size, self.backdrop)
        return Image.composite(frame, back, outline).resize(size, Image.LANCZOS)

    def shell(self, size, shell, marks=(), fine=True):
        """The whole player, its screen dark. fine=False is quick and a little soft: for while the
        window's size is still changing."""
        key = (size, shell)
        if key in self._fine:
            img = self._fine[key]
        elif fine:
            scale = size[0] / DEVICE_SIZE[0]
            over = 3 if scale <= 1.3 else 2
            img = self._on_backdrop(*_draw_shell(shell, scale * over), size)
            if len(self._fine) > 6:
                self._fine.clear()
            self._fine[key] = img
        else:
            if not self._rough or self._rough[:2] != ('base', shell):
                self._rough = ('base', shell, self._on_backdrop(*_draw_shell(shell, 2), (DEVICE_SIZE[0] * 2, DEVICE_SIZE[1] * 2)))
            img = self._rough[2].resize(size, Image.BILINEAR)
        if self._base[:2] != (size, id(img)):
            self._base = (size, id(img), shell, img, img.crop(self.screen_box(size)))  # what the screen is laid on
        return self._with_marks(img, marks, size, (0, 0))

    def screen_box(self, size):
        s = size[0] / DEVICE_SIZE[0]
        return (round(SCREEN_AT[0] * s), round(SCREEN_AT[1] * s),
                round((SCREEN_AT[0] + SCREEN_SIZE[0]) * s), round((SCREEN_AT[1] + SCREEN_SIZE[1]) * s))

    def screen(self, size, shell, screen, marks=()):
        """The screen's picture for a player of this size, its rounded corners showing the player's
        frame behind them (the player as shell() last drew it)."""
        if (self._base[0], self._base[2]) != (size, shell):
            self.shell(size, shell, fine=False)
        x0, y0, x1, y1 = self.screen_box(size)
        want = (x1 - x0, y1 - y0)
        if want == screen.size:
            scaled = screen
        else:
            ratio = want[0] / screen.size[0]
            how = Image.LANCZOS if ratio < 1 else Image.NEAREST if ratio == int(ratio) else Image.BICUBIC
            scaled = screen.resize(want, how)
        if want not in self._masks:
            if len(self._masks) > 6:
                self._masks.clear()
            big = Image.new('L', (want[0] * 3, want[1] * 3))
            ImageDraw.Draw(big).rounded_rectangle((0, 0, big.size[0] - 1, big.size[1] - 1),
                                                  SCREEN_CORNER * 3 * want[0] / SCREEN_SIZE[0], fill=255)
            self._masks[want] = big.resize(want, Image.LANCZOS)
        out = Image.composite(scaled, self._base[4], self._masks[want])
        return self._with_marks(out, marks, size, (x0, y0)), (x0, y0)

    def _with_marks(self, img, marks, size, origin):
        """img with the marks (kind, x, y, radius in the drawing's own coordinates) that fall on it."""
        if not marks:
            return img
        s = size[0] / DEVICE_SIZE[0]
        out = None
        for kind, x, y, r in marks:
            key = (kind, round(r * s))
            if key not in self._sprites:
                if len(self._sprites) > 24:
                    self._sprites.clear()
                self._sprites[key] = _sprite(kind, r * s)
            sprite = self._sprites[key]
            px, py = round(x * s) - origin[0] - sprite.size[0] // 2, round(y * s) - origin[1] - sprite.size[1] // 2
            if px + sprite.size[0] <= 0 or py + sprite.size[1] <= 0 or px >= img.size[0] or py >= img.size[1]:
                continue
            if out is None:
                out = img.convert('RGBA')
            out.alpha_composite(sprite, (max(px, 0), max(py, 0)),
                                (max(-px, 0), max(-py, 0), min(sprite.size[0], img.size[0] - px), min(sprite.size[1], img.size[1] - py)))
        return img if out is None else out.convert('RGB')
