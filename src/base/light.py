"""
The light model, for products that are lights (PlasmaStick, the Unicorn HAT): on or off, a mode with its parameters,
a brightness (1-100%), and fades between them. The product gives its modes beyond Colour (MODES: name to an effect
class) and its LEDs (an output with count and show(frame)); the light draws frames and hands them over.

A frame is a list of (r, g, b), 0-255, one per LED, already scaled for brightness and fades. The output does what's
particular to its LEDs: gamma, the order of the pixels, a power limit.

Effects are classes made with (count, parameters, light) that give frame(now) -> the next frame at full brightness
(now in ms), with their Speed (1-10) in parameters if they take one. Optionally they have check(parameters) -> why
the parameters can't be used (or None), and `finished`, which turns the light off once it's true (a sequence that
ends, such as a race start).

An alert interrupts whatever the light is doing, on or off: it flashes a colour for a while, then the light carries
on as it was. The alert settings say how, and whether alerts are switched on (a test alert works either way).
"""
from base.compat import asyncio, ticks_ms, ticks_diff, sleep_ms
from base.log import Log
from base.settings import Setting, is_int, is_bool

TAG = 'LIGHT'

MIN_KELVIN, MAX_KELVIN = 2000, 6500
DEFAULT_COLOUR = '#ffb46b'  # a warm white
# A frame every 20 ms while something moves (a fade or an animation)
FRAME_MS = 20
_HEX_DIGITS = '0123456789abcdefABCDEF'


def is_valid_colour(value):
    """True for a '#RRGGBB' (or 'RRGGBB') hex colour string."""
    if not isinstance(value, str):
        return False
    digits = value[1:] if value.startswith('#') else value
    return len(digits) == 6 and all(c in _HEX_DIGITS for c in digits)


def rgb(colour):
    digits = colour[1:] if colour.startswith('#') else colour
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def hex_colour(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(int(r), int(g), int(b))


def is_valid_kelvin(value):
    return isinstance(value, int) and not isinstance(value, bool) and MIN_KELVIN <= value <= MAX_KELVIN


def kelvin_colour(kelvin):
    """The '#rrggbb' of white light at kelvin (the usual fit to the black-body curve)."""
    from math import log
    t = kelvin / 100
    if t <= 66:
        r, g = 255, 99.4708025861 * log(t) - 161.1195681661
    else:
        r, g = 329.698727446 * (t - 60) ** -0.1332047592, 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255 if t >= 66 else 0 if t <= 19 else 138.5177312231 * log(t - 10) - 305.0447927307
    return '#{:02x}{:02x}{:02x}'.format(*(min(255, max(0, int(v + 0.5))) for v in (r, g, b)))


def _is_colour(value):
    return None if is_valid_colour(value) else 'must be a #RRGGBB hex colour'


# The settings every light has, next to BASE_SETTINGS (the product adds the default mode's choices)
LIGHT_SETTINGS = {
    'device': {
        'default_state': Setting(True, is_bool),
        'default_mode': Setting('Colour'),
        'default_colour': Setting(DEFAULT_COLOUR, _is_colour),
        'default_brightness': Setting(66, is_int(1, 100)),
        # Turning on and off, and changing the brightness or colour, fade over this long (0: at once)
        'transition_ms': Setting(500, is_int(0, 10000)),
        # For animations started without a speed of their own (as from Home Assistant, or a group)
        'effect_speed': Setting(5, is_int(1, 10)),
    },
    'alert': {
        # Whether alerts interrupt the light (the switch); a test alert works either way
        'enabled': Setting(False, is_bool),
        'colour': Setting('#ff0000', _is_colour),
        'seconds': Setting(10, is_int(1, 3600)),
        # Each flash: this long on, then this long off (0: steady)
        'on_ms': Setting(500, is_int(50, 10000)),
        'off_ms': Setting(500, is_int(0, 10000)),
        'brightness': Setting(100, is_int(1, 100)),
    },
}


class Light:
    def __init__(self, output, settings, modes=None, product=None, min_level=0.06):
        """output: the LEDs (count, show(frame)); modes: the product's effects by name; min_level: the share of full
        output that 1% brightness draws (the dimmest a full channel still shows on these LEDs)."""
        self.output = output
        self.settings = settings
        self.effects = modes or {}
        # In the product's order (MODE_ORDER), where it gives one: a MicroPython dict may not keep it
        self.modes = ('Colour',) + tuple(getattr(product, 'MODE_ORDER', None) or self.effects)
        self.product = product
        self.min_level = min_level
        self.on = False
        self.mode = 'Colour'
        self.parameters = {'Colour': settings.get('device', 'default_colour')}
        self.brightness = settings.get('device', 'default_brightness')
        # Told of every change (MQTT state, groups): listener(light); light.local says whether it was made here
        # (rather than by following a group, which isn't passed back to it)
        self.listeners = []
        self.local = True
        self._effect = None
        # What's drawn: the level (0-1, brightness and fades) and, in Colour mode, the colour, each fading to its target
        self._level = 0.0
        self._level_fade = None  # (from, to, start, ms)
        self._colour = rgb(self.parameters['Colour'])
        self._colour_fade = None  # (from rgb, to rgb, start, ms)
        self._wake = asyncio.Event()
        self._alert = None  # (rgb, start, ms, on_ms, off_ms, level) while one shows
        default_mode = settings.get('device', 'default_mode')
        error = self.set(on=settings.get('device', 'default_state'),
                         mode=default_mode if default_mode in self.modes else 'Colour', transition_ms=0)
        if error:
            Log.warn(TAG, 'Default mode: ' + error)

    # Changes

    def parameters_error(self, mode, parameters):
        """Why parameters can't be used for mode, or None if they can."""
        if mode not in self.modes:
            return 'Mode must be one of ' + ', '.join(self.modes)
        if parameters is None:
            return None
        if not isinstance(parameters, dict):
            return 'Mode parameters must be an object'
        if 'Colour' in parameters and not is_valid_colour(parameters['Colour']):
            return 'Colour must be a #RRGGBB hex colour'
        if mode == 'Colour':
            if 'Kelvin' in parameters and not is_valid_kelvin(parameters['Kelvin']):
                return 'Kelvin must be a whole number, {}-{}'.format(MIN_KELVIN, MAX_KELVIN)
            return None
        speed = parameters.get('Speed')
        if speed is not None and (not isinstance(speed, int) or isinstance(speed, bool) or not 1 <= speed <= 10):
            return 'Speed must be a whole number, 1-10'
        check = getattr(self.effects[mode], 'check', None)
        return check(parameters) if check else None

    def set(self, on=None, mode=None, parameters=None, brightness=None, transition_ms=None, local=True):
        """Changes what the light does; anything None stays as it is. local: made here, rather than by following a
        group. Returns why it couldn't, or None."""
        if mode is not None or parameters is not None:
            new_mode = self.mode if mode is None else mode
            error = self.parameters_error(new_mode, parameters)
            if error:
                return error
        if brightness is not None and (not isinstance(brightness, int) or isinstance(brightness, bool)
                                       or not 1 <= brightness <= 100):
            return 'Brightness must be a whole number, 1-100'
        fade = self.settings.get('device', 'transition_ms') if transition_ms is None else transition_ms
        before = self.state()

        if mode is not None and mode != self.mode:
            self.mode = mode
            # A new mode starts from its defaults; Colour keeps its colour
            self.parameters = {'Colour': hex_colour(*self._colour)} if mode == 'Colour' else {}
            self._effect = None
        if parameters:
            if self.mode == 'Colour' and 'Kelvin' in parameters:
                parameters = dict(parameters, Colour=kelvin_colour(parameters['Kelvin']))
            elif self.mode == 'Colour' and 'Colour' in parameters:
                self.parameters.pop('Kelvin', None)
            self.parameters.update(parameters)
            if self._effect is not None and hasattr(self._effect, 'update'):
                self._effect.update(self.parameters)
            elif any(key in parameters for key in getattr(self.effects.get(self.mode), 'remakes', ())):
                self._effect = None  # made again with them (an effect class's remakes, such as a style)
        if self.mode == 'Colour':
            self._fade_colour(rgb(self.parameters['Colour']), fade)
        if brightness is not None:
            self.brightness = brightness
        if on is not None:
            self.on = on
        self._fade_level(self._target_level(), fade)

        if self.state() != before:
            Log.info(TAG, 'Now {}'.format(self.state()))
            self._told(local)
        self._wake.set()
        return None

    def speed(self):
        """The current animation's speed (1-10): its own, or the setting's."""
        return self.parameters.get('Speed') or self.settings.get('device', 'effect_speed')

    def alert(self, colour=None, seconds=None, on_ms=None, off_ms=None, brightness=None, test=False):
        """Starts an alert (or starts it again), with the alert settings for anything not given. Returns why it
        couldn't, or None. Unless it's a test, it needs alerts switched on."""
        def setting(key, value):
            return self.settings.get('alert', key) if value is None else value
        if not test and not self.settings.get('alert', 'enabled'):
            return 'Alerts are switched off'
        colour, seconds, brightness = setting('colour', colour), setting('seconds', seconds), setting('brightness', brightness)
        on_ms, off_ms = setting('on_ms', on_ms), setting('off_ms', off_ms)
        if not is_valid_colour(colour):
            return 'Colour must be a #RRGGBB hex colour'
        for name, value, low, high in (('Seconds', seconds, 1, 3600), ('OnMs', on_ms, 50, 10000),
                                       ('OffMs', off_ms, 0, 10000), ('Brightness', brightness, 1, 100)):
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                return '{} must be a whole number, {}-{}'.format(name, low, high)
        started = self._alert is None
        level = self.min_level + (1 - self.min_level) * (brightness - 1) / 99
        self._alert = (rgb(colour), ticks_ms(), seconds * 1000, on_ms, off_ms, level)
        Log.info(TAG, 'Alert: {} for {} s'.format(colour, seconds))
        if started:
            self._told()
        self._wake.set()
        return None

    def stop_alert(self):
        if self._alert is not None:
            self._alert = None
            Log.info(TAG, 'Alert over')
            self._told()
            self._wake.set()

    @property
    def alerting(self):
        return self._alert is not None

    def _told(self, local=True):
        self.local = local
        for listener in self.listeners:
            listener(self)
        self.local = True

    def toggle(self):
        self.set(on=not self.on)
        return self.on

    def state(self):
        """As PlasmaStick's MQTT state: {"State", "Mode", "ModeParameters", "Brightness"}. An effect class can say what
        it uses when a parameter isn't given (defaults(light), such as the speed), which the state shows too."""
        parameters = dict(self.parameters)
        defaults = getattr(self.effects.get(self.mode), 'defaults', None)
        if defaults is not None:
            for key, value in defaults(self).items():
                parameters.setdefault(key, value)
        return {'State': self.on, 'Mode': self.mode, 'ModeParameters': parameters, 'Brightness': self.brightness}

    # Drawing

    def _target_level(self):
        if not self.on:
            return 0.0
        return self.min_level + (1 - self.min_level) * (self.brightness - 1) / 99

    def _fade_level(self, target, ms):
        self._level_fade = (self._level, target, ticks_ms(), ms) if ms > 0 else None
        if ms <= 0:
            self._level = target

    def _fade_colour(self, target, ms):
        # Only while it's showing: switched off, the colour just changes
        if ms > 0 and self._level > 0 and target != self._colour:
            self._colour_fade = (self._colour, target, ticks_ms(), ms)
        else:
            self._colour, self._colour_fade = target, None

    def _step(self, now):
        """Moves the fades on; True while anything is still moving."""
        moving = False
        if self._level_fade:
            start_level, end_level, start, ms = self._level_fade
            done = min(1, ticks_diff(now, start) / ms)
            self._level = start_level + (end_level - start_level) * done
            self._level_fade = None if done >= 1 else self._level_fade
            moving = moving or done < 1
        if self._colour_fade:
            start_rgb, end_rgb, start, ms = self._colour_fade
            done = min(1, ticks_diff(now, start) / ms)
            self._colour = tuple(a + (b - a) * done for a, b in zip(start_rgb, end_rgb))
            self._colour_fade = None if done >= 1 else self._colour_fade
            moving = moving or done < 1
        return moving

    def frame(self, now):
        """The frame to show now, and whether the light is moving (another frame is due soon)."""
        moving = self._step(now)
        if self._alert is not None:
            colour, start, ms, on_ms, off_ms, level = self._alert
            elapsed = ticks_diff(now, start)
            if elapsed < ms:
                lit = elapsed % (on_ms + off_ms) < on_ms
                return [tuple(c * level for c in colour) if lit else (0, 0, 0)] * self.output.count, True
            self.stop_alert()
        level = self._level
        if level <= 0:
            return [(0, 0, 0)] * self.output.count, moving
        if self.mode == 'Colour':
            pixel = tuple(c * level for c in self._colour)
            return [pixel] * self.output.count, moving
        effect = self.effect()
        pixels = effect.frame(now)
        if getattr(effect, 'finished', False):
            self._effect = None
            self.set(on=False, transition_ms=0)
            return [(0, 0, 0)] * self.output.count, True
        return [tuple(c * level for c in p) for p in pixels], True

    def effect(self):
        """The current mode's effect (made when first needed), or None in Colour mode."""
        if self._effect is None and self.mode != 'Colour':
            self._effect = self.effects[self.mode](self.output.count, self.parameters, self)
        return self._effect

    async def run(self):
        """Draws while anything moves, and waits for a change otherwise."""
        if hasattr(self.output, 'paint'):
            await self._run_direct()
            return
        last = None
        while True:
            pixels, moving = self.frame(ticks_ms())
            if pixels != last:
                try:
                    self.output.show(pixels)
                except Exception as e:
                    Log.error(TAG, 'Showing a frame: {}'.format(e))
                last = pixels
            if moving:
                await sleep_ms(FRAME_MS)
            else:
                self._wake.clear()
                await self._wake.wait()

    async def _run_direct(self):
        """For an output that draws itself (a Pico's LED strip, where a frame of new tuples each time would keep the
        garbage collector busy): it paints one colour at a level (paint(rgb, level), clear()), and an effect draws
        straight into its strip (frame(strip, level) -> the ms to the next frame, with its speed set first), so a frame
        allocates nothing. Nothing is drawn while nothing changes."""
        out = self.output
        painted = None  # what's on the LEDs, when it's one colour
        while True:
            now = ticks_ms()
            moving = self._step(now)
            wait = None
            if self._alert is not None:
                colour, start, ms, on_ms, off_ms, level = self._alert
                elapsed = ticks_diff(now, start)
                if elapsed < ms:
                    look = (colour, level) if elapsed % (on_ms + off_ms) < on_ms else ((0, 0, 0), 0)
                    if look != painted:
                        out.paint(*look)
                        painted = look
                    wait = FRAME_MS
                else:
                    self.stop_alert()
                    continue
            if wait is None:
                level = self._level
                try:
                    if level <= 0:
                        if painted != 'off':
                            out.clear()
                            painted = 'off'
                    elif self.mode == 'Colour':
                        look = (self._colour, level)
                        if look != painted:
                            out.paint(*look)
                            painted = look
                    else:
                        effect = self.effect()
                        if hasattr(effect, 'speed'):
                            effect.speed = self.speed()
                        wait = effect.frame(out.strip, level)
                        painted = None
                        if getattr(effect, 'finished', False):
                            self._effect = None
                            self.set(on=False, transition_ms=0)
                except Exception as e:
                    Log.error(TAG, 'Drawing: {}'.format(e))
                    wait = 100
            if wait is not None:
                await sleep_ms(wait)
            elif moving:
                await sleep_ms(FRAME_MS)
            else:
                self._wake.clear()
                await self._wake.wait()
