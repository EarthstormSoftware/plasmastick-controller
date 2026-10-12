import time
import plasma
import uasyncio as asyncio
import asynchttp
from log import Log
from deviceconfig import DEFAULT_COLOUR, CHRISTMAS_STYLES

TAG = "LEDS"

# The animated modes, drawn by effects.py (its EFFECTS has the same names).
EFFECT_MODES = ('Rainbows', 'Fire', 'Ocean', 'Christmas')
# The lighting modes the strip supports.
MODES = ('Colour', 'CheerLights') + EFFECT_MODES

_HEX_DIGITS = '0123456789abcdefABCDEF'


def is_valid_colour(value):
    """True for a '#RRGGBB' (or 'RRGGBB') hex colour string."""
    if not isinstance(value, str):
        return False
    digits = value[1:] if value.startswith('#') else value
    return len(digits) == 6 and all(c in _HEX_DIGITS for c in digits)


# The plasma driver gamma-corrects every value it's given (set_rgb and set_hsv),
# so its input is already perceptual, but anything below 22 comes out as 0.
MIN_VISIBLE = 22


def light_output(percent):
    """The share (0-1) of full driver input for percent brightness: 1% is the
    dimmest level a full-on channel still shows, 100% is full."""
    return (MIN_VISIBLE + (255 - MIN_VISIBLE) * (percent - 1) / 99) / 255


def mode_parameters_error(mode, parameters):
    """Why parameters can't be used for mode, or None if they can."""
    if parameters is None:
        return None
    if not isinstance(parameters, dict):
        return 'Mode parameters must be an object'
    if mode == 'Colour' and 'Colour' in parameters and not is_valid_colour(parameters['Colour']):
        return 'Colour must be a #RRGGBB hex colour'
    if mode == 'Christmas' and 'Style' in parameters and parameters['Style'] not in CHRISTMAS_STYLES:
        return 'Style must be one of ' + ', '.join(CHRISTMAS_STYLES)
    return None

class LedStrip:
    def __init__(self, device_config):
        self.device_config = device_config
        device_color_order = plasma.COLOR_ORDER_RGB
        if (self.device_config.device_led_rgbw):
            device_color_order = plasma.COLOR_ORDER_GRB
        self.led_strip = plasma.WS2812(device_config.device_led_count, rgbw=self.device_config.device_led_rgbw, color_order=device_color_order)
        self.led_strip.start()

        self.current_state = False
        self.next_state = False
        self.previous_state = False
        self.last_time_state_changed = None
        self.current_mode = "Colour"
        self.next_mode = "Colour"
        self.previous_mode = "Colour"
        self.mode_parameters = None
        self.last_time_mode_changed = None

        # Cheerlights specific data
        self.cheerlights_last_update = 0

        # The running animation (see effects.py), and the layout it was made for.
        self._effect = None
        self._effect_layout = None
        self._effect_reverse = None
        self._effect_style = None

        # Cache for hex color conversion
        self._cached_hex_colour = None
        self._cached_rgb = (255, 255, 255)

        # CheerLights async task tracking
        self._cheerlights_task = None

        # The colour actually showing ('#rrggbb'), for state reports. None in
        # the animated modes, or before CheerLights has fetched one.
        self.current_colour = None
        # Set whenever what's showing changes, so state can be published.
        self.changed = asyncio.Event()

        # Set whenever a new state, mode or mode parameters are requested, so the
        # controller applies and repaints once instead of every loop.
        self._dirty = True

        # Brightness in percent, applied by scaling every colour painted. A change
        # only repaints: it doesn't restart an animation or refetch CheerLights.
        self.brightness = device_config.default_brightness
        self._output = light_output(self.brightness)
        self._repaint = False

        # Transitions. _drawn is the output actually being drawn: it fades to
        # _output (or to 0 when switching off) over device.transition_ms, or
        # over the next change's own time (Home Assistant's transition).
        self._drawn = 0.0
        self._fade = None  # (from, to, start ticks, ms) while fading
        self._fading_out = False  # switched off, still fading to dark
        self._next_transition = None
        # Colour mode: the colour being drawn, and its cross-fade to a new one.
        self._shown_rgb = None
        self._colour_fade = None  # (from rgb, to rgb, start ticks, ms)

        self.set_next_state(self.device_config.default_state)
        self.set_next_mode(self.device_config.default_mode, device_config.mode_parameters)

        Log.info(TAG, 'Default state: {}, default mode: {}'.format(self.current_state, self.current_mode))

    def set_state(self, state):
        self.previous_state = self.current_state
        self.current_state = state
        self.last_time_state_changed = time.time()

    def set_next_state(self, state, transition_ms=None):
        self.next_state = state
        self._dirty = True
        if transition_ms is not None:
            self._next_transition = transition_ms

    def set_brightness(self, brightness, transition_ms=None):
        self.brightness = brightness
        self._output = light_output(brightness)
        self._repaint = True
        if transition_ms is not None:
            self._next_transition = transition_ms
        self.changed.set()

    def set_mode(self, mode):
        self.previous_mode = self.current_mode
        self.current_mode = mode
        self.last_time_mode_changed = time.time()

    def set_next_mode(self, mode, parms=None, transition_ms=None):
        self.next_mode = mode
        self.mode_parameters = parms
        self._dirty = True
        if transition_ms is not None:
            self._next_transition = transition_ms

    def set_next_transition(self, ms):
        """Fade the next change over ms instead of device.transition_ms."""
        self._next_transition = ms

    def _take_transition(self):
        """The fade time for the change being applied: the request's own, else the setting."""
        ms = self._next_transition
        self._next_transition = None
        return self.device_config.transition_ms if ms is None else ms

    def _fade_to(self, target, ms):
        if ms > 0 and target != self._drawn:
            self._fade = (self._drawn, target, time.ticks_ms(), ms)
        else:
            self._drawn, self._fade = target, None

    def _step_fades(self):
        """Move the fades on to now. True if that changed what to draw: while one
        runs, and once more as it finishes, so the final value gets painted."""
        moved = False
        if self._fade:
            moved = True
            a, b, start, ms = self._fade
            t = time.ticks_diff(time.ticks_ms(), start)
            if t >= ms:
                self._drawn, self._fade = b, None
            else:
                self._drawn = a + (b - a) * t / ms
        if self._colour_fade:
            moved = True
            a, b, start, ms = self._colour_fade
            t = time.ticks_diff(time.ticks_ms(), start)
            if t >= ms:
                self._shown_rgb, self._colour_fade = b, None
            else:
                self._shown_rgb = tuple(x + (y - x) * t // ms for x, y in zip(a, b))
        return moved

    @property
    def state_will_change(self):
        return not (self.current_state == self.next_state)

    @property
    def mode_will_change(self):
        return self.current_mode != self.next_mode

    @property
    def will_change(self):
        return self.state_will_change or self.mode_will_change

    def set_off(self):
        Log.info(TAG, 'Setting LED strip to: OFF')
        self.led_strip.clear()

    def hex_to_rgb(self, hex_colour):
        # converts a hex colour code into RGB with caching
        # Only convert if color changed
        if hex_colour != self._cached_hex_colour:
            offset = 1 if hex_colour[0] == '#' else 0
            if len(hex_colour) - offset != 6:
                raise ValueError('Invalid colour length')
            self._cached_rgb = (
                int(hex_colour[offset:offset+2], 16),
                int(hex_colour[offset+2:offset+4], 16),
                int(hex_colour[offset+4:offset+6], 16)
            )
            self._cached_hex_colour = hex_colour
        return self._cached_rgb

    def report(self):
        """What the strip is showing: {'state', 'mode', 'colour'}."""
        return {'state': self.current_state, 'mode': self.current_mode, 'colour': self.current_colour,
                'style': self.style(), 'brightness': self.brightness}

    def style(self):
        """The Christmas style showing (the request's, else the default), or None in other modes."""
        if self.current_mode != 'Christmas':
            return None
        style = self.mode_parameters.get('Style') if isinstance(self.mode_parameters, dict) else None
        return style if style in CHRISTMAS_STYLES else self.device_config.christmas_style

    def paint(self, colour):
        """Set every LED to colour ('#rrggbb', or an (r, g, b) tuple), scaled by the
        output being drawn (the brightness, or a fade towards it)."""
        r, g, b = self.hex_to_rgb(colour) if isinstance(colour, str) else colour
        f = self._drawn
        r, g, b = int(r * f + 0.5), int(g * f + 0.5), int(b * f + 0.5)
        for i in range(self.device_config.device_led_count):
            self.led_strip.set_rgb(i, r, g, b)

    async def _fetch_cheerlights(self):
        """Fetch the CheerLights colour without blocking the event loop."""
        # Counts as an update even if it fails, so a failing URL is retried at
        # the normal interval rather than on every controller loop.
        self.cheerlights_last_update = time.time()
        try:
            Log.debug(TAG, 'Requesting URL: {}'.format(self.device_config.cheerlights_url))
            payload = await asynchttp.get_json(self.device_config.cheerlights_url)
            hex_colour = payload.get('field2') if isinstance(payload, dict) else None
            if not is_valid_colour(hex_colour):
                # Not a "latest entry" feed (or an empty one): keep what's showing
                # rather than guess.
                Log.warn(TAG, 'CheerLights reply has no usable colour in field2; keeping the current colour')
                return
            if not hex_colour.startswith('#'):
                hex_colour = '#' + hex_colour
            # The strip may have been switched off or changed mode while waiting.
            if not (self.current_state and self.current_mode == 'CheerLights') or self._fading_out:
                return
            self.paint(hex_colour)
            Log.info(TAG, 'LEDs set to {}'.format(hex_colour))
            if hex_colour != self.current_colour:
                self.current_colour = hex_colour
                self.changed.set()
        except Exception as e:
            Log.warn(TAG, 'CheerLights update failed: {!r}'.format(e))

    def _animation(self):
        """The effect for the current mode, made afresh after a mode change or
        when the LED layout or Christmas style changes (they apply without a restart)."""
        dc = self.device_config
        style = self.style()
        if (self._effect is None or dc.led_layout != self._effect_layout or dc.led_reverse != self._effect_reverse
                or style != self._effect_style):
            import effects
            reverse = dc.led_reverse and dc.led_layout != 'bottle'
            self._effect = effects.EFFECTS[self.current_mode](dc.device_led_count, dc.led_layout, reverse, style)
            self._effect_layout, self._effect_reverse, self._effect_style = dc.led_layout, dc.led_reverse, style
        return self._effect

    async def controller(self):
        while True:
            try:
                repaint = self._repaint
                self._repaint = False
                ms = self._take_transition() if (repaint or self._dirty) else 0
                if self._dirty:
                    self._dirty = False
                    repaint = True
                    was_colour = self.current_state and self.current_mode == 'Colour' and not self._fading_out

                    if self.state_will_change:
                        if self.next_state:
                            if not self._fading_out:
                                self._drawn = 0.0  # fade in from dark
                            self._fading_out = False
                        elif ms > 0:
                            self._fading_out = True  # keep drawing while it fades to dark
                        else:
                            self.set_off()
                        self.set_state(self.next_state)

                    if self.mode_will_change:
                        self.set_mode(self.next_mode)
                        was_colour = False

                    Log.debug(TAG, 'State / Mode change, so reinitialise')
                    if self.current_mode == 'Colour':
                        parms = self.mode_parameters if isinstance(self.mode_parameters, dict) else {}
                        self.current_colour = parms.get('Colour', DEFAULT_COLOUR)
                        new_rgb = self.hex_to_rgb(self.current_colour)
                        if was_colour and self._shown_rgb is not None and ms > 0 and new_rgb != self._shown_rgb:
                            self._colour_fade = (self._shown_rgb, new_rgb, time.ticks_ms(), ms)
                        else:
                            self._shown_rgb, self._colour_fade = new_rgb, None
                    elif self.current_mode != 'CheerLights':
                        self.current_colour = None
                    # CheerLights keeps the last fetched colour until a new one arrives.
                    self.changed.set()
                    self.cheerlights_last_update = 0
                    self._effect = None  # animations start afresh

                if repaint:
                    self._fade_to(self._output if self.current_state else 0.0, ms)

                fading = self._step_fades()
                if self._fading_out and not fading:
                    self._fading_out = False
                    self.set_off()
                # Light on (or still fading to dark): update
                if self.current_state or self._fading_out:
                    # While fading, static modes repaint often enough to look smooth.
                    wait = 20 if fading else 100

                    # Set all LEDs to static colour
                    if self.current_mode == 'Colour':
                        if repaint or fading:
                            self.paint(self._shown_rgb)
                        await asyncio.sleep_ms(wait)

                    # Set LEDs according to CheerLights
                    elif self.current_mode == 'CheerLights':
                        currentTime = time.time()
                        if (repaint or fading) and self.current_colour and self.cheerlights_last_update:
                            self.paint(self.current_colour)  # a new brightness
                        if (self.current_state and
                                currentTime - self.cheerlights_last_update > self.device_config.cheerlights_update_interval):
                            # Spawn async task for HTTP request to avoid blocking event loop
                            if not self._cheerlights_task or self._cheerlights_task.done():
                                self._cheerlights_task = asyncio.create_task(self._fetch_cheerlights())
                        await asyncio.sleep_ms(wait)

                    # Animations: one frame, then wait as long as the effect asks
                    elif self.current_mode in EFFECT_MODES:
                        await asyncio.sleep_ms(self._animation().frame(self.led_strip, self._drawn))

                    else:
                        await asyncio.sleep_ms(100)
                else:
                    # Strip is off: nothing to draw until the next request.
                    await asyncio.sleep_ms(100)
            except Exception as e:
                Log.error(TAG, 'LED controller loop error: {}'.format(e))
                await asyncio.sleep_ms(100)
