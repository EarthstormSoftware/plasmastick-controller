"""The Plasma Stick's LED strip, as an App Base light's output that draws itself (see base.light's _run_direct):
paint() sets every LED to one colour at a level, clear() turns them off, and the effects (effects.py) draw straight
into .strip. The plasma driver sends its buffer to the LEDs by itself, and gamma-corrects what it's given, so its input
is already perceptual; anything below MIN_VISIBLE comes out as 0.
"""
import plasma

MIN_VISIBLE = 22


class Strip:
    def __init__(self, count, rgbw):
        self.count = count
        self.strip = plasma.WS2812(count, rgbw=rgbw, color_order=plasma.COLOR_ORDER_GRB if rgbw else
                                   plasma.COLOR_ORDER_RGB)
        self.strip.start()

    def paint(self, colour, level):
        """colour (r, g, b, 0-255, perhaps mid-fade) at level (the share, 0-1, of full output) on every LED."""
        r, g, b = (int(c * level + 0.5) for c in colour)
        for i in range(self.count):
            self.strip.set_rgb(i, r, g, b)

    def clear(self):
        self.strip.clear()
