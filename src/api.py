import gc
import time
import uasyncio as asyncio
import ujson
import machine
import network
from log import Log
from deviceconfig import DeviceConfig, DEVICE_NAME_FILE, is_valid_brightness, mqtt_topics
import clock
from deviceidentity import sanitize_device_name
from ledstrip import MODES, mode_parameters_error
from version import VERSION

TAG = "API"

# Device settings that are read where they're used, so a save applies them at
# once. Everything else is only read at boot and needs a restart.
LIVE_DEVICE_KEYS = ('default_mode', 'mode_parameters', 'default_state', 'default_brightness',
                    'cheerlights_url', 'cheerlights_update_interval', 'log_level', 'led_layout', 'led_reverse',
                    'christmas_style', 'transition_ms', 'effect_speed', 'utc_offset_min', 'dst',
                    'latitude', 'longitude', 'schedule')


def restart_settings(cfg):
    """A copy of the parts of a config that only take effect after a restart.
    Compared with ==, as MicroPython doesn't keep dict key order."""
    dev = cfg.get('device', {})
    return ujson.loads(ujson.dumps([{k: v for k, v in dev.items() if k not in LIVE_DEVICE_KEYS},
                                    cfg.get('wlan', {}), cfg.get('mqtt', {})]))

def _settings(name, *args):
    # settings.py is only needed while settings are read or saved: load it for
    # the call and let it go again, to keep its code out of memory otherwise.
    import sys
    import settings
    try:
        return getattr(settings, name)(*args)
    finally:
        del sys.modules['settings']


class DeviceAPI:
    """
    REST API and internal control interface for PlasmaStick.
    Exposes endpoints for device discovery, control, and configuration.
    """

    def __init__(self, device_config, led_strip, pico_led):
        self.device_config = device_config
        self.led_strip = led_strip
        self.pico_led = pico_led
        # The name in use (MQTT client id, hostname) is device_config.device_name.
        # A rename is saved here and takes effect on the next restart.
        self.device_name = device_config.device_name
        # Saves start from the config loaded at boot (config.json merged with
        # defaults), so they also fill in any keys an older file was missing.
        # A copy, so changes here don't alter the live settings in device_config.
        self._config_cache = ujson.loads(ujson.dumps(device_config.configdata))
        self._running_settings = restart_settings(self._config_cache)
        # Checked on every HTTP request; updated as soon as a new one is saved.
        self.web_password_hash = device_config.web_password_hash
        # The last Authorization header that matched it, so a browser's repeated
        # requests don't each pay for the hash. Cleared when the password changes.
        self.accepted_auth = None
        self._started = time.time()
        self.version = VERSION
        # Set by main.py: the MQTT client (None when MQTT is off) and the last
        # connection error, so the web UI can show why MQTT isn't connected.
        self.mqtt_client = None
        self.mqtt_error = None
        # Set by main.py: how long the firmware had stalled when the watchdog
        # last restarted the board, or None.
        self.watchdog_stall = None
        # Set by every change made here (web UI, API, button, MQTT control, Home
        # Assistant), so app.py can pass it on to the device's group.
        self.local_change = asyncio.Event()
        # True once app.py has set the clock from NTP (until then it says 2021).
        self.clock_set = False
        # Seconds ahead of UTC for local time (time zone and daylight saving); app.py keeps it up to date.
        self.utc_offset = 0

    def shift_clock(self, seconds):
        """The clock was just moved by seconds: keep the uptime right."""
        self._started += seconds

    def set_device_name(self, name):
        """Save device name. It takes effect (MQTT client id, hostname) after a restart."""
        if not name or len(name) == 0 or len(name) > 32:
            return False

        safe_name = sanitize_device_name(name)
        if len(safe_name) > 32:
            return False

        try:
            with open(DEVICE_NAME_FILE, 'w') as f:
                ujson.dump({'name': safe_name}, f)
            self.device_name = safe_name
            Log.info(TAG, f'Device name set to: {safe_name}')
            self._retire_old_topics()
            return True
        except OSError as e:
            Log.error(TAG, f'Failed to save device name: {e}')
            return False

    def get_device_info(self):
        """Return device info and health as dict."""
        gc.collect()  # so mem_free is live data, not live data plus garbage
        sta = network.WLAN(network.STA_IF)
        try:
            rssi = sta.status('rssi') if sta.isconnected() else None
        except (OSError, ValueError):
            rssi = None
        client = self.mqtt_client
        return {
            'name': self.device_name,
            'device_id': self.device_config.device_name,
            'version': VERSION,
            'state': self.led_strip.current_state,
            'mode': self.led_strip.current_mode,
            'colour': self.led_strip.current_colour,
            'style': self.led_strip.style(),
            'speed': self.led_strip.speed(),
            'kelvin': self.led_strip.kelvin(),
            'brightness': self.led_strip.brightness,
            'modes': MODES,
            'led_count': self.device_config.device_led_count,
            'uptime_s': time.time() - self._started,
            'time': clock.iso(time.time(), self.utc_offset) if self.clock_set else None,
            'wifi': {'ssid': self.device_config.wlanconfig.ssid, 'ip': sta.ifconfig()[0], 'rssi': rssi},
            'mqtt': {
                'enabled': client is not None,
                'connected': bool(client and client.isconnected()),
                'error': self.mqtt_error,
            },
            'watchdog_stall': self.watchdog_stall,
            'mem_free': gc.mem_free(),
            'mem_alloc': gc.mem_alloc(),
        }

    def power_on(self):
        """Turn device on."""
        self.led_strip.set_next_state(True)
        self.local_change.set()
        Log.info(TAG, 'Power ON')
        return True

    def power_off(self):
        """Turn device off."""
        self.led_strip.set_next_state(False)
        self.local_change.set()
        Log.info(TAG, 'Power OFF')
        return True

    def toggle_power(self):
        """Toggle power state."""
        # Toggle from requested state (next_state) so rapid repeated toggles
        # are deterministic even before LED controller applies current_state.
        new_state = not self.led_strip.next_state
        self.led_strip.set_next_state(new_state)
        self.local_change.set()
        Log.info(TAG, 'Power toggled to: {}'.format('ON' if new_state else 'OFF'))
        return new_state

    def set_mode(self, mode, parameters=None):
        """Set LED mode. Returns False for an unknown mode or unusable parameters."""
        if mode in MODES:
            error = mode_parameters_error(mode, parameters)
            if error:
                Log.warn(TAG, f'Rejected mode {mode}: {error}')
                return False
            if parameters:
                self.led_strip.set_next_mode(mode, parameters)
            else:
                self.led_strip.set_next_mode(mode, self.device_config.mode_parameters)
            self.local_change.set()
            Log.info(TAG, f'Mode set to: {mode}')
            return True
        return False

    def set_brightness(self, brightness):
        """Set brightness in percent, 1-100; fractions are rounded. Returns False if invalid."""
        if isinstance(brightness, float):
            brightness = int(brightness + 0.5) if brightness >= 0 else -1
        if not is_valid_brightness(brightness):
            return False
        self.led_strip.set_brightness(brightness)
        self.local_change.set()
        Log.info(TAG, f'Brightness set to: {brightness}%')
        return True

    def get_config(self):
        """The settings, as GET /api/config shows them (no passwords)."""
        return _settings('view', self)

    def save_config(self, updates):
        """Check updates and merge them into config.json. Returns (success, error)."""
        return _settings('save', self, updates)

    def _retire_old_topics(self):
        """After a save or rename, note the retained topics in use now that the
        saved settings won't use, so they're cleared after the restart."""
        mq = self._config_cache['mqtt']
        new = mqtt_topics(self.device_name, mq['topic_base'], mq['ha_prefix'])
        import mqttstate
        retired = mqttstate.retired_topics(self.device_config.mqttconfig.topics(), new)
        if retired:
            try:
                DeviceConfig.retire_topics(retired)
            except OSError as e:  # not worth failing the save over
                Log.warn(TAG, f'Could not note the old MQTT topics: {e}')

    def restart_required(self):
        """True if saved settings (or a rename) differ from those running."""
        return (self.device_name != self.device_config.device_name or
                restart_settings(self._config_cache) != self._running_settings)

    def reset(self, keep_wifi):
        """Reset settings to defaults (see DeviceConfig.reset). Restart afterwards."""
        # The name and topics after the reset aren't known until the next boot, so
        # note every retained topic in use: any still in use then aren't cleared.
        import mqttstate
        t = self.device_config.mqttconfig.topics()
        try:
            DeviceConfig.retire_topics([t[k] for k in mqttstate.RETAINED])
        except OSError as e:
            Log.warn(TAG, f'Could not note the old MQTT topics: {e}')
        DeviceConfig.reset(keep_wifi)

    def restart(self):
        """Soft-reset the device. Does not return."""
        Log.info(TAG, 'Restarting device...')
        import watchdog
        watchdog.disable()  # the next boot starts its own
        machine.soft_reset()

    def set_mqtt_enabled(self, enabled):
        """Persist MQTT enabled flag to config.json and update live config object."""
        try:
            # Use cached config instead of reloading from file
            cfg = self._config_cache
            cfg.setdefault('mqtt', {})['enabled'] = bool(enabled)
            DeviceConfig.saveConfigToFile(cfg)
            self.device_config.mqttconfig.enabled = bool(enabled)
            Log.info(TAG, f'MQTT enabled set to: {bool(enabled)}')
            return True, None
        except Exception as e:
            Log.error(TAG, f'Failed to set MQTT enabled: {e}')
            return False, str(e)
