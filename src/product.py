"""PlasmaStick Controller as an App Base product: an LED strip on a Pimoroni Plasma Stick 2040 W.

The base does the light (on or off, a mode, brightness, fades, alerts, groups, the schedule), the web page and API,
the settings, MQTT and Home Assistant, Wi-Fi, setup mode, updates and the clock (base/pico). This is what's
PlasmaStick's own: the strip (strip.py), its animations (effects.py, drawn straight into the strip), CheerLights, the
button (button.py), its settings, and its config.json's layout as 1.4 wrote it (Codec), so a device carries on and
1.4 can still read the file if an update rolls back.
"""
from base.log import Log
from base.settings import Setting, is_bool, is_int, is_one_of
from version import VERSION  # noqa: F401  (scripts/wifi_update.py reads it there)

TAG = 'PLASMA'
NAME = 'PlasmaStick'
SETUP_SSID = 'PlasmaStick-SETUP'
NAME_PREFIX = 'PicoLights-'
MANUFACTURER = 'Pimoroni'
MODEL = 'Plasma Stick 2040 W'
# The dimmest the strip shows: the plasma driver gamma-corrects, so a channel below 22 of 255 is 0
MIN_LEVEL = 22 / 255

LED_LAYOUTS = ('bottle', 'horizontal', 'vertical')
CHRISTMAS_STYLES = ('Twinkle', 'Fade')
CHEERLIGHTS_URL = 'http://api.thingspeak.com/channels/1417/field/2/last.json'
# The modes after Colour, in 1.4's order (Home Assistant's effect list)
MODE_ORDER = ('CheerLights', 'Rainbows', 'Fire', 'Ocean', 'Christmas')


def _url_error(value):
    if not isinstance(value, str) or not (value.startswith('http://') or value.startswith('https://')) or \
            len(value) > 200:
        return 'must be an http:// or https:// URL'
    return None


SETTINGS = {
    'device': {
        # The strip: how many LEDs, and whether they're RGBW
        'device_led_count': Setting(10, is_int(1, 300), restart=True),
        'device_led_rgbw': Setting(False, is_bool, restart=True),
        # 1.4's defaults, where they differ from the base's
        'default_state': Setting(False, is_bool),
        'default_mode': Setting('Colour', is_one_of('Colour', *MODE_ORDER)),
        # How the LEDs are laid out, for the animations; led_reverse flips a strip (vertical: the first LED at the top)
        'led_layout': Setting('bottle', is_one_of(*LED_LAYOUTS)),
        'led_reverse': Setting(False, is_bool),
        # The Christmas style when a request doesn't name one
        'christmas_style': Setting('Twinkle', is_one_of(*CHRISTMAS_STYLES)),
        # http: a TLS handshake holds the CPU for about a second
        'cheerlights_url': Setting(CHEERLIGHTS_URL, _url_error),
        'cheerlights_update_interval': Setting(60, is_int(10, 86400)),
    },
}
# The strip, not a preference: a reset keeps it
RESET_KEEPS = (('device', 'device_led_count'), ('device', 'device_led_rgbw'))


class Codec:
    """config.json as 1.4 laid it out: the default colour in device.mode_parameters, and the schedule and location in
    device, not a section of their own. After the Pico's own part (base.pico.Codec)."""

    @staticmethod
    def load(stored):
        stored = dict(stored)
        device = dict(stored.get('device') or {})
        if 'schedule' not in stored or not isinstance(stored['schedule'], dict):
            stored['schedule'] = {'events': device.pop('schedule', []), 'latitude': device.pop('latitude', None),
                                  'longitude': device.pop('longitude', None)}
        parameters = device.pop('mode_parameters', None)
        if isinstance(parameters, dict) and parameters.get('Colour') and 'default_colour' not in device:
            device['default_colour'] = parameters['Colour']
        stored['device'] = device
        return stored

    @staticmethod
    def dump(values):
        stored = dict(values)
        device = dict(values['device'])
        schedule = stored.pop('schedule')
        device['schedule'] = schedule['events']
        device['latitude'], device['longitude'] = schedule['latitude'], schedule['longitude']
        device['mode_parameters'] = {'Colour': device['default_colour']}
        stored['device'] = device
        return stored


class _Animation:
    """One of effects.py's animations, made for the strip's layout; effects.py is loaded when one first runs."""
    remakes = ()

    def __init__(self, name):
        self.name = name

    def __call__(self, count, parameters, light):
        import effects
        s = light.settings
        layout = s.get('device', 'led_layout')
        reverse = s.get('device', 'led_reverse') and layout != 'bottle'
        return getattr(effects, self.name)(count, layout, reverse, self.style(light, parameters))

    def style(self, light, parameters):
        return None

    def defaults(self, light):
        return {'Speed': light.speed()}


class _Christmas(_Animation):
    remakes = ('Style',)

    def style(self, light, parameters):
        return parameters.get('Style') or light.settings.get('device', 'christmas_style')

    def defaults(self, light):
        return {'Speed': light.speed(), 'Style': self.style(light, light.parameters)}

    def check(self, parameters):
        if 'Style' in parameters and parameters['Style'] not in CHRISTMAS_STYLES:
            return 'Style must be one of ' + ', '.join(CHRISTMAS_STYLES)
        return None


_cheerlights_colour = None  # the last one fetched, shown until a new one comes


class _CheerLightsDrawing:
    """The colour the world chose (CheerLights), on every LED: drawn again only when it or the level changes."""

    def __init__(self, light):
        self.light = light
        self.shown = None

    def frame(self, strip, level):
        colour = self.light.parameters.get('Colour') or _cheerlights_colour
        if colour and (colour, level) != self.shown:
            self.shown = (colour, level)
            from base.light import rgb
            r, g, b = (int(c * level + 0.5) for c in rgb(colour))
            for i in range(self.light.output.count):
                strip.set_rgb(i, r, g, b)
        return 100


class _CheerLights:
    def __call__(self, count, parameters, light):
        return _CheerLightsDrawing(light)

    def defaults(self, light):
        # The last colour fetched, in the state until a new one comes (as 1.4)
        return {'Colour': _cheerlights_colour} if _cheerlights_colour else {}


LIGHT_MODES = {'CheerLights': _CheerLights(), 'Rainbows': _Animation('Rainbows'), 'Fire': _Animation('Fire'),
               'Ocean': _Animation('Ocean'), 'Christmas': _Christmas('Christmas')}


def info(device):
    """1.4's light fields in /api/info, next to the base's light, so what reads them carries on."""
    light = device.light
    p = light.state()['ModeParameters']
    return {'state': light.on, 'mode': light.mode, 'colour': p.get('Colour'), 'style': p.get('Style'),
            'speed': p.get('Speed'), 'kelvin': p.get('Kelvin'), 'brightness': light.brightness,
            'modes': light.modes, 'led_count': light.output.count}


def output(device):
    from strip import Strip
    s = device.settings
    return Strip(s.get('device', 'device_led_count'), s.get('device', 'device_led_rgbw'))


def setup_held():
    from button import held_at_start
    return held_at_start()


def tasks(device):
    from button import Button
    from base.compat import asyncio
    wake = asyncio.Event()
    device.light.listeners.append(lambda light: _changed(light, wake))
    return [Button(device).run(), _cheerlights(device, wake)]


def _changed(light, wake):
    # effects.py (and its tables) only stays while an animation needs it: about 6 KB of a Pico's heap
    if not isinstance(LIGHT_MODES.get(light.mode), _Animation):
        from base.compat import release
        release('effects')
    wake.set()  # CheerLights may be showing now


def applied(device, changed):
    # The animations are made for the layout and style: a change makes the running one again
    if any(key in (('device', 'led_layout'), ('device', 'led_reverse'), ('device', 'christmas_style'))
           for key in changed):
        device.light._effect = None
        device.light._wake.set()


async def _cheerlights(device, wake):
    """Fetches the CheerLights colour while the light shows it: on going into the mode, then every
    cheerlights_update_interval seconds. Otherwise it waits for a change to the light (wake)."""
    global _cheerlights_colour
    import time
    from base.compat import sleep_ms
    light, s = device.light, device.settings
    fetched_at, showing = None, False
    while True:
        now = time.time()
        if light.on and light.mode == 'CheerLights':
            if not showing or fetched_at is None or now - fetched_at >= s.get('device', 'cheerlights_update_interval'):
                fetched_at = now  # a failure waits the interval too, rather than retrying at once
                colour = await _fetch(s.get('device', 'cheerlights_url'))
                if colour and light.mode == 'CheerLights':
                    _cheerlights_colour = colour
                    if light.parameters.get('Colour') != colour:
                        Log.info(TAG, 'CheerLights: {}'.format(colour))
                        light.set(parameters={'Colour': colour})
            showing = True
            await sleep_ms(1000)
        else:
            showing = False
            wake.clear()
            await wake.wait()


async def _fetch(url):
    """The colour in a CheerLights feed (its latest entry's field2), or None."""
    from base.light import is_valid_colour
    try:
        import asynchttp
        payload = await asynchttp.get_json(url)
    except Exception as e:
        Log.warn(TAG, 'CheerLights update failed: {!r}'.format(e))
        return None
    finally:
        from base.compat import release
        release('asynchttp')  # only needed now and then
    colour = payload.get('field2') if isinstance(payload, dict) else None
    if not is_valid_colour(colour):
        # Not a "latest entry" feed (or an empty one): keep what's showing rather than guess
        Log.warn(TAG, 'CheerLights reply has no usable colour in field2; keeping the current colour')
        return None
    return colour.lower() if colour.startswith('#') else '#' + colour.lower()
