"""Animated modes, drawn a frame at a time by LedStrip's controller.

Each effect is a class made for the strip's LED count and layout. Its
frame(strip, output) draws one frame and returns the milliseconds to wait
before the next; output is the share (0-1) of full driver input for the
brightness (see ledstrip.light_output). Frames use whole numbers and buffers
made up front, so drawing one allocates nothing: on the Pico every float is a
new object for the garbage collector to clear up.

Layouts (device.led_layout): 'bottle' (LEDs jumbled), 'horizontal' and
'vertical' strips. reverse flips a strip's direction: a vertical strip's first
LED is then at the top, and a horizontal strip's patterns travel the other way.

Imported the first time an animated mode runs, so it costs no heap until then.
"""

import math
from random import getrandbits

FRAME_MS = 33  # about 30 frames a second

# One turn of a sine wave in 256 steps, 0-255 centred on 128. Made once, here,
# so frames only look values up.
SINE = bytearray(int(128 + 127 * math.sin(2 * math.pi * i / 256)) for i in range(256))


def rand(n):
    """A random whole number from 0 to n - 1 (n up to 256)."""
    return (getrandbits(8) * n) >> 8


def scaled_table(colour, output):
    """256 colours from colour(i) (0-255 each) as r, g, b bytes, scaled to the brightness."""
    level = int(output * 256 + 0.5)
    t = bytearray(768)
    for i in range(256):
        r, g, b = colour(i)
        t[3 * i] = (r * level + 128) >> 8
        t[3 * i + 1] = (g * level + 128) >> 8
        t[3 * i + 2] = (b * level + 128) >> 8
    return t


def hue_rgb(h):
    """The full-saturation colour at hue h (0-255) as (r, g, b), each 0-255."""
    s = h * 6
    sector, f = s >> 8, s & 255
    q = 255 - f
    return ((255, f, 0), (q, 255, 0), (0, 255, f), (0, q, 255), (f, 0, 255), (255, 0, q))[sector]


class Rainbows:
    """One full rainbow along the strip, turning slowly."""

    # Hue moved per frame, in 1/256ths of a colour-table step: a full turn
    # takes about 6.7 s, as it did at 60 frames a second.
    STEP = 328

    def __init__(self, n, layout, reverse):
        self.n = n
        self.offset = 0  # 8.8 fixed point: the high byte is the table step
        # Where each LED sits around the colour wheel.
        self.pos = bytearray((n - 1 - i if reverse else i) * 256 // n for i in range(n))
        self.table = None  # r, g, b for each of 256 hues, at the current brightness
        self.output = -1

    def frame(self, strip, output):
        if output != self.output:
            self.table, self.output = scaled_table(hue_rgb, output), output
        self.offset = (self.offset + self.STEP) & 0xFFFF
        off, t, pos = self.offset >> 8, self.table, self.pos
        for i in range(self.n):
            k = ((pos[i] + off) & 255) * 3
            strip.set_rgb(i, t[k], t[k + 1], t[k + 2])
        return FRAME_MS


def heat_rgb(h):
    """Flame colour for heat h (0-255): dark red, red, orange, amber, then a hint of white."""
    if h < 128:
        return (h * 2, 0, 0)
    if h < 224:
        return (255, (h - 128) * 170 // 96, 0)
    return (255, 170 + (h - 224) * 85 // 32, (h - 224) * 3)


class Fire:
    """Flames, shaped by the layout: a bottle of embers, flames rising up a
    vertical strip, or a row of flame tips along a horizontal one."""

    def __init__(self, n, layout, reverse):
        self.n = n
        self.reverse = reverse
        self.heat = bytearray(n)
        self.table = None
        self.output = -1
        if layout == 'vertical':
            self.step = self._rise
            # Taller strips cool less per LED, so the flames reach about as high.
            self.cooling = min(255, 550 // n + 2)
        else:
            self.step = self._flicker if layout == 'bottle' else self._row
            self.target = bytearray(n)
            self.out = bytearray(n)
            self.t = 0
            for i in range(n):
                self.heat[i] = self.target[i] = 100 + rand(100)

    def frame(self, strip, output):
        if output != self.output:
            self.table, self.output = scaled_table(heat_rgb, output), output
        self.step(strip)
        return FRAME_MS

    def _draw(self, strip, heat, flip):
        t, last = self.table, self.n - 1
        for i in range(self.n):
            k = heat[i] * 3
            strip.set_rgb(last - i if flip else i, t[k], t[k + 1], t[k + 2])

    def _ease(self):
        # Each LED eases towards its own target heat and picks a new one on
        # arrival (or now and then early): mostly glowing, sometimes flaring.
        heat, target = self.heat, self.target
        for i in range(self.n):
            h, g = heat[i], target[i]
            if h == g or getrandbits(4) == 0:
                g = target[i] = 215 + rand(40) if getrandbits(5) == 0 else 80 + rand(120)
            heat[i] = h + (g - h) // 3 if abs(g - h) > 2 else g

    def _flicker(self, strip):
        self._ease()
        self._draw(strip, self.heat, False)

    def _row(self, strip):
        # The flicker, plus two slow waves moving hot and cool patches along
        # the strip, blurred so neighbours move together like one flame front.
        self._ease()
        self.t = (self.t + 1) & 0xFFFF
        heat, out, n, t = self.heat, self.out, self.n, self.t
        for i in range(n):
            w = SINE[(i * 9 - t) & 255] + SINE[(i * 23 - t - (t >> 1)) & 255] - 256  # about -254 to 254
            out[i] = min(255, max(40, heat[i] + (w >> 2)))
        prev = out[0]
        for i in range(n):
            cur = out[i]
            out[i] = (prev + 2 * cur + (out[i + 1] if i + 1 < n else cur)) >> 2
            prev = cur
        self._draw(strip, out, self.reverse)

    def _rise(self, strip):
        # "Fire2012": every cell cools a little, heat drifts up and spreads,
        # and new sparks start near the bottom (heat[0]).
        heat, n = self.heat, self.n
        for i in range(n):
            c = rand(self.cooling)
            heat[i] = heat[i] - c if heat[i] > c else 0
        for k in range(n - 1, 1, -1):
            heat[k] = (heat[k - 1] + 2 * heat[k - 2]) // 3
        if getrandbits(8) < 120:
            y = rand(min(7, n))
            heat[y] = min(255, heat[y] + 160 + rand(96))
        self._draw(strip, heat, self.reverse)


# The animated modes, by name. ledstrip.EFFECT_MODES lists the same names.
EFFECTS = {'Rainbows': Rainbows, 'Fire': Fire}
