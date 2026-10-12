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

FRAME_MS = 33  # about 30 frames a second


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
        self.table = bytearray(768)  # r, g, b for each of 256 hues, at the current brightness
        self.output = -1

    def _build(self, output):
        level = int(output * 256 + 0.5)
        t = self.table
        for h in range(256):
            r, g, b = hue_rgb(h)
            t[3 * h] = (r * level + 128) >> 8
            t[3 * h + 1] = (g * level + 128) >> 8
            t[3 * h + 2] = (b * level + 128) >> 8
        self.output = output

    def frame(self, strip, output):
        if output != self.output:
            self._build(output)
        self.offset = (self.offset + self.STEP) & 0xFFFF
        off, t, pos = self.offset >> 8, self.table, self.pos
        for i in range(self.n):
            k = ((pos[i] + off) & 255) * 3
            strip.set_rgb(i, t[k], t[k + 1], t[k + 2])
        return FRAME_MS


# The animated modes, by name. ledstrip.EFFECT_MODES lists the same names.
EFFECTS = {'Rainbows': Rainbows}
