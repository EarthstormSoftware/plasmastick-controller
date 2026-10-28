"""
The device: a product, the platform it runs on, its settings, its light (if it's one) and its web server.

A product is a module (product.py) with:
  NAME, VERSION                 what the page and /api/info show
  MANUFACTURER, MODEL           optional: the hardware, for Home Assistant (default: Earthstorm Software, NAME)
  SETTINGS                      its own settings ({section: {key: Setting}}), next to the base's
  LIGHT_MODES (lights only)     its effects by name ({} for Colour only); with it, the product is a light
  output(device) (lights only)  its LEDs: an object with count and show(frame), or one that draws itself (paint,
                                clear and strip: see Light._run_direct)
  MIN_LEVEL (lights only)       optional: the share of full output that 1% brightness draws (default 0.06)
  MODE_ORDER (lights only)      optional: the modes' order, after Colour (a MicroPython dict may not keep LIGHT_MODES')
  setup_held()                  optional, on a Pico: True to start setup mode at power-on (a button held, say)
  Codec                         optional: load(stored) and dump(values) for its part of a settings file laid out
                                its own way (its firmware before the base), after the platform's
  info(device)                  optional: its own fields for /api/info
  routes(app, device)           optional: its own API routes
  applied(device, changed)      optional: settings changed ((section, key) pairs) that apply at once
  tasks(device)                 optional: coroutines to run alongside
  state(device)                 optional, not lights: its state for MQTT's state topic (call device.state_changed())
  set(device, command)          optional, not lights: a command from MQTT's set topic (a dict); returns an error or None
  scheduled(device, on)         optional, not lights: what a schedule event does (base.schedule), so it has one
  HA_ENTITIES, ha_entity(device, key, topics)   optional: its own Home Assistant entities, as (component, key), and
                                each one's discovery config (unique_id, availability and device are added)
  www                           optional: the folder with its index.html (default: www beside product.py)

main.py starts it: run(product).
"""
from base.compat import MICROPYTHON, asyncio, mem_free, collect, beside
from base.log import Log
from base.settings import Settings, BASE_SETTINGS, merge

TAG = 'DEVICE'
PORT = 80


class _Both:
    """The platform's codec and the product's, for a file laid out by both (a Pico product's firmware before the
    base): loading takes the platform's part first, saving the product's."""

    def __init__(self, platform, product):
        self.platform, self.product = platform, product

    def load(self, stored):
        return self.product.load(self.platform.load(stored))

    def dump(self, values):
        return self.platform.dump(self.product.dump(values))


def _codec(platform, product):
    if platform is not None and product is not None:
        return _Both(platform, product)
    return platform or product


class Device:
    def __init__(self, product, platform):
        self.product = product
        self.platform = platform
        is_light = hasattr(product, 'LIGHT_MODES')
        from base.mqtt import MQTT_SETTINGS
        # The platform's after the base's (a Pico's MQTT password replaces the base's), and the product's last
        schemas = [BASE_SETTINGS, MQTT_SETTINGS, getattr(platform, 'SETTINGS', {}), product.SETTINGS]
        self.scheduled = is_light or hasattr(product, 'scheduled')
        if self.scheduled and not is_light:
            from base.schedule import schedule_settings
            schemas.insert(-1, schedule_settings(()))
        if is_light:
            from base.light import LIGHT_SETTINGS
            from base.schedule import schedule_settings
            from base.lightmqtt import LIGHT_MQTT_SETTINGS
            schemas[1:1] = [LIGHT_SETTINGS, schedule_settings(('Colour',) + tuple(product.LIGHT_MODES)),
                            LIGHT_MQTT_SETTINGS]
        self.settings = Settings(platform.state_path('config.json'), merge(*schemas),
                                 codec=_codec(getattr(platform, 'Codec', None), getattr(product, 'Codec', None)))
        Log.level = self.settings.get('device', 'log_level')
        self.www = getattr(product, 'www', None) or beside(product.__file__, 'www')
        self.light = None
        if is_light:
            from base.light import Light
            self.light = Light(product.output(self), self.settings, product.LIGHT_MODES, product,
                               getattr(product, 'MIN_LEVEL', 0.06))
        self.mqtt = None  # until it's made, as making it asks info() which figures there are
        if is_light:
            from base.lightmqtt import LightMqtt as Mqtt
        else:
            from base.mqtt import Mqtt
        self.mqtt = Mqtt(self)

    def info(self):
        collect()
        info = {
            'product': self.product.NAME,
            'version': self.product.VERSION,
            'name': self.platform.device_name(),
            'name_editable': self.platform.NAME_EDITABLE,
            'mem_free': mem_free(),
        }
        info.update(self.platform.health())
        if self.light is not None:
            light = self.light
            info['light'] = {
                'state': light.on,
                'mode': light.mode,
                'parameters': light.state()['ModeParameters'],  # with an effect's defaults, as MQTT has them
                'brightness': light.brightness,
                'modes': light.modes,
                'alert': {'enabled': self.settings.get('alert', 'enabled'), 'active': light.alerting},
            }
        if self.mqtt is not None:
            info['mqtt'] = self.mqtt.status()
        if hasattr(self.product, 'info'):
            info.update(self.product.info(self))
        return info

    def applied(self, changed):
        """Settings that changed and apply at once (the rest wait for the restart)."""
        if ('device', 'log_level') in changed:
            Log.level = self.settings.get('device', 'log_level')
        if ('alert', 'enabled') in changed:
            # Switching alerts off silences one that's showing
            if not self.settings.get('alert', 'enabled'):
                self.light.stop_alert()
            self.mqtt.alert_switched()
        if hasattr(self.product, 'applied'):
            self.product.applied(self, changed)

    def state_changed(self):
        """The product's state changed: MQTT publishes it."""
        if self.mqtt is not None:
            self.mqtt.state_changed()

    def reset(self):
        Log.warn(TAG, 'Resetting the settings')
        # Except the ones that describe the hardware rather than a preference (RESET_KEEPS: (section, key) pairs)
        self.settings.reset(getattr(self.product, 'RESET_KEEPS', ()))
        if hasattr(self.platform, 'reset'):
            self.platform.reset()

    async def restart_soon(self):
        # Long enough for the response to go
        await asyncio.sleep(0.3)
        Log.info(TAG, 'Restarting')
        self.platform.restart()

    async def main(self):
        # The light and the product's own tasks first: they work without the network
        tasks = []
        if self.light is not None:
            tasks.append(asyncio.create_task(self.light.run()))
        if hasattr(self.product, 'tasks'):
            tasks += [asyncio.create_task(task) for task in self.product.tasks(self)]
        if hasattr(self.platform, 'start'):
            await self.platform.start(self)  # a Pico's Wi-Fi, and the rest that needs it
        from base import web
        app = web.create(self)
        port = getattr(self.platform, 'PORT', PORT)
        server = asyncio.create_task(app.start_server(host=None if not MICROPYTHON else '0.0.0.0', port=port))
        tasks.append(server)
        if hasattr(self.platform, 'serving'):
            asyncio.create_task(self.platform.serving(server))
        if self.scheduled:
            from base import schedule
            tasks.append(asyncio.create_task(schedule.run(self)))
        if self.mqtt.enabled:
            tasks.append(asyncio.create_task(self.mqtt.run()))
        else:
            Log.info(TAG, 'MQTT is off (no broker, or mqtt.enabled is false)')
        if hasattr(self.platform, 'watchdog'):
            tasks.append(asyncio.create_task(self.platform.watchdog()))
        Log.info(TAG, '{} {} on port {}'.format(self.product.NAME, self.product.VERSION, port))
        await asyncio.gather(*tasks)


def run(product):
    if MICROPYTHON:
        from base import pico
        pico.run(product)
        return
    from base import pi as platform
    device = Device(product, platform)
    asyncio.run(device.main())
