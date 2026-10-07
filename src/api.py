import ujson
import machine
from log import Log
from deviceconfig import DeviceConfig, DEVICE_NAME_FILE
from deviceidentity import sanitize_device_name
from ledstrip import MODES

TAG = "API"

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
        # Cache config to reduce file I/O allocations on saves
        self._config_cache = self._load_config_cache()

    def _load_config_cache(self):
        """Load config from file once at startup and cache it."""
        try:
            with open('config.json') as fp:
                return ujson.load(fp)
        except Exception as e:
            Log.error(TAG, f'Failed to load config cache: {e}')
            return {}
    
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
            return True
        except OSError as e:
            Log.error(TAG, f'Failed to save device name: {e}')
            return False
    
    def get_device_info(self):
        """Return device info as dict."""
        return {
            'name': self.device_name,
            'device_id': self.device_config.device_name,
            'state': self.led_strip.current_state,
            'mode': self.led_strip.current_mode,
            'led_count': self.device_config.device_led_count,
        }
    
    def power_on(self):
        """Turn device on."""
        self.led_strip.set_next_state(True)
        Log.info(TAG, 'Power ON')
        return True
    
    def power_off(self):
        """Turn device off."""
        self.led_strip.set_next_state(False)
        Log.info(TAG, 'Power OFF')
        return True
    
    def toggle_power(self):
        """Toggle power state."""
        # Toggle from requested state (next_state) so rapid repeated toggles
        # are deterministic even before LED controller applies current_state.
        new_state = not self.led_strip.next_state
        self.led_strip.set_next_state(new_state)
        Log.info(TAG, 'Power toggled to: {}'.format('ON' if new_state else 'OFF'))
        return new_state
    
    def set_mode(self, mode, parameters=None):
        """Set LED mode."""
        if mode in MODES:
            if parameters:
                self.led_strip.set_next_mode(mode, parameters)
            else:
                self.led_strip.set_next_mode(mode, self.device_config.mode_parameters)
            Log.info(TAG, f'Mode set to: {mode}')
            return True
        return False

    def get_config(self):
        """Return current config as dict, omitting passwords."""
        dc = self.device_config
        return {
            'device': {
                'device_name': dc.device_name,
                'device_led_count': dc.device_led_count,
                'device_led_rgbw': dc.device_led_rgbw,
                'default_mode': dc.default_mode,
                'mode_parameters': dc.mode_parameters,
                'default_state': dc.default_state,
                'cheerlights_url': dc.cheerlights_url,
                'cheerlights_update_interval': dc.cheerlights_update_interval,
            },
            'wlan': {
                'ssid': dc.wlanconfig.ssid,
                'connect_timeout': dc.wlanconfig.connect_timeout,
                'reconnect_wait': dc.wlanconfig.reconnect_wait,
                'max_retries': dc.wlanconfig.max_retries,
            },
            'mqtt': {
                'enabled': dc.mqttconfig.enabled,
                'broker_address': dc.mqttconfig.broker_address,
                'user': dc.mqttconfig.user,
                'clean': dc.mqttconfig.clean,
                'queue_len': dc.mqttconfig.queue_len,
            },
        }

    def save_config(self, updates):
        """
        Merge validated updates into config.json using cached config to reduce I/O.
        Only recognised fields are accepted; passwords must be non-empty strings if provided.
        Returns (success, error_message).
        """
        try:
            # Work on a copy of the cached config, so a rejected update can't leave
            # half-applied changes behind for the next save.
            cfg = ujson.loads(ujson.dumps(self._config_cache))
            for section in ('device', 'wlan', 'mqtt'):
                cfg.setdefault(section, {})

            dev = updates.get('device', {})
            wlan = updates.get('wlan', {})
            mqtt = updates.get('mqtt', {})

            # Device section
            if 'device_led_count' in dev:
                v = int(dev['device_led_count'])
                if v < 1 or v > 300:
                    return False, 'device_led_count must be 1-300'
                cfg['device']['device_led_count'] = v
            if 'device_led_rgbw' in dev:
                cfg['device']['device_led_rgbw'] = bool(dev['device_led_rgbw'])
            if 'default_mode' in dev:
                if dev['default_mode'] not in MODES:
                    return False, 'Invalid default_mode'
                cfg['device']['default_mode'] = dev['default_mode']
            if 'mode_parameters' in dev:
                cfg['device']['mode_parameters'] = dev['mode_parameters']
            if 'default_state' in dev:
                cfg['device']['default_state'] = bool(dev['default_state'])
            if 'cheerlights_url' in dev:
                cfg['device']['cheerlights_url'] = str(dev['cheerlights_url'])
            if 'cheerlights_update_interval' in dev:
                v = int(dev['cheerlights_update_interval'])
                if v < 10:
                    return False, 'cheerlights_update_interval must be >= 10'
                cfg['device']['cheerlights_update_interval'] = v

            # WLAN section (SSID comes from provisioning, not config.json)
            if 'connect_timeout' in wlan:
                v = int(wlan['connect_timeout'])
                if v < 5:
                    return False, 'connect_timeout must be >= 5'
                cfg['wlan']['connect_timeout'] = v
            if 'reconnect_wait' in wlan:
                v = int(wlan['reconnect_wait'])
                if v < 5:
                    return False, 'reconnect_wait must be >= 5'
                cfg['wlan']['reconnect_wait'] = v
            if 'max_retries' in wlan:
                v = int(wlan['max_retries'])
                if v < 0:
                    return False, 'max_retries must be >= 0'
                cfg['wlan']['max_retries'] = v

            # MQTT section
            if 'enabled' in mqtt:
                cfg['mqtt']['enabled'] = bool(mqtt['enabled'])
            if 'broker_address' in mqtt:
                cfg['mqtt']['broker_address'] = str(mqtt['broker_address'])
            if 'user' in mqtt:
                cfg['mqtt']['user'] = str(mqtt['user'])
            if 'password' in mqtt:
                pw = str(mqtt['password'])
                if len(pw) == 0:
                    return False, 'MQTT password cannot be empty'
                cfg['mqtt']['password'] = pw
            if 'clean' in mqtt:
                cfg['mqtt']['clean'] = bool(mqtt['clean'])
            if 'queue_len' in mqtt:
                v = int(mqtt['queue_len'])
                if v < 1:
                    return False, 'queue_len must be >= 1'
                cfg['mqtt']['queue_len'] = v

            DeviceConfig.saveConfigToFile(cfg)
            self._config_cache = cfg
            Log.info(TAG, 'Config saved.')
            return True, None

        except Exception as e:
            Log.error(TAG, f'Config save error: {e}')
            return False, str(e)

    def restart(self):
        """Soft-reset the device. Does not return."""
        Log.info(TAG, 'Restarting device...')
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
