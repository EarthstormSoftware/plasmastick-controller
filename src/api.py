import gc
import time
import ujson
import machine
import network
from log import Log
from deviceconfig import DeviceConfig, DEVICE_NAME_FILE, is_valid_brightness, mqtt_topics
from passwords import hash_password
from wificrypto import encrypt_password
from deviceidentity import sanitize_device_name
from ledstrip import MODES, mode_parameters_error
from asynchttp import parse_url
from version import VERSION

TAG = "API"

# Device settings that are read where they're used, so a save applies them at
# once. Everything else is only read at boot and needs a restart.
LIVE_DEVICE_KEYS = ('default_mode', 'mode_parameters', 'default_state', 'default_brightness',
                    'cheerlights_url', 'cheerlights_update_interval', 'log_level')


def restart_settings(cfg):
    """A copy of the parts of a config that only take effect after a restart.
    Compared with ==, as MicroPython doesn't keep dict key order."""
    dev = cfg.get('device', {})
    return ujson.loads(ujson.dumps([{k: v for k, v in dev.items() if k not in LIVE_DEVICE_KEYS},
                                    cfg.get('wlan', {}), cfg.get('mqtt', {})]))

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
        # Set by main.py: the MQTT client (None when MQTT is off) and the last
        # connection error, so the web UI can show why MQTT isn't connected.
        self.mqtt_client = None
        self.mqtt_error = None
    
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
            'brightness': self.led_strip.brightness,
            'led_count': self.device_config.device_led_count,
            'uptime_s': time.time() - self._started,
            'wifi': {'ssid': self.device_config.wlanconfig.ssid, 'ip': sta.ifconfig()[0], 'rssi': rssi},
            'mqtt': {
                'enabled': client is not None,
                'connected': bool(client and client.isconnected()),
                'error': self.mqtt_error,
            },
            'mem_free': gc.mem_free(),
            'mem_alloc': gc.mem_alloc(),
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
        Log.info(TAG, f'Brightness set to: {brightness}%')
        return True

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
                'default_brightness': dc.default_brightness,
                'cheerlights_url': dc.cheerlights_url,
                'cheerlights_update_interval': dc.cheerlights_update_interval,
                'log_level': dc.log_level,
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
                'ha_discovery': dc.mqttconfig.ha_discovery,
                'topic_base': self._config_cache['mqtt']['topic_base'],
                'ha_prefix': self._config_cache['mqtt']['ha_prefix'],
            },
            'web': {
                'password_set': bool(self.web_password_hash),
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
                error = mode_parameters_error('Colour', dev['mode_parameters'])
                if error or dev['mode_parameters'] is None:
                    return False, error or 'Mode parameters must be an object'
                cfg['device']['mode_parameters'] = dev['mode_parameters']
            if 'default_state' in dev:
                cfg['device']['default_state'] = bool(dev['default_state'])
            if 'default_brightness' in dev:
                v = dev['default_brightness']
                if not is_valid_brightness(v):
                    return False, 'default_brightness must be a whole number, 1-100'
                cfg['device']['default_brightness'] = v
            if 'cheerlights_url' in dev:
                url = str(dev['cheerlights_url']).strip()
                try:
                    parse_url(url)
                except ValueError:
                    return False, 'cheerlights_url must be an http:// or https:// URL'
                cfg['device']['cheerlights_url'] = url
            if 'cheerlights_update_interval' in dev:
                v = int(dev['cheerlights_update_interval'])
                if v < 10:
                    return False, 'cheerlights_update_interval must be >= 10'
                cfg['device']['cheerlights_update_interval'] = v

            if 'log_level' in dev:
                v = int(dev['log_level'])
                if v not in (0, 1, 2):
                    return False, 'log_level must be 0, 1 or 2'
                cfg['device']['log_level'] = v

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
                cfg['mqtt'].pop('password', None)
                cfg['mqtt']['password_iv'], cfg['mqtt']['password_enc'] = encrypt_password(pw)
            if 'clean' in mqtt:
                cfg['mqtt']['clean'] = bool(mqtt['clean'])
            if 'ha_discovery' in mqtt:
                cfg['mqtt']['ha_discovery'] = bool(mqtt['ha_discovery'])
            if 'queue_len' in mqtt:
                v = int(mqtt['queue_len'])
                if v < 1:
                    return False, 'queue_len must be >= 1'
                cfg['mqtt']['queue_len'] = v
            if 'topic_base' in mqtt or 'ha_prefix' in mqtt:
                import mqttstate
                for key, check in (('topic_base', mqttstate.topic_base_error), ('ha_prefix', mqttstate.ha_prefix_error)):
                    if key in mqtt:
                        v = str(mqtt[key]).strip()
                        error = check(v)
                        if error:
                            return False, error
                        cfg['mqtt'][key] = v

            # Web section: set a new password, or clear it.
            web_updates = updates.get('web', {})
            if web_updates.get('clear_password'):
                cfg['web'] = {'password_hash': ''}
            elif 'password' in web_updates:
                pw = str(web_updates['password'])
                if len(pw) < 4:
                    return False, 'Web password must be at least 4 characters'
                cfg['web'] = {'password_hash': hash_password(pw)}

            DeviceConfig.saveConfigToFile(cfg)
            self._config_cache = cfg
            new_hash = cfg.get('web', {}).get('password_hash', '')
            if new_hash != self.web_password_hash:
                self.web_password_hash = new_hash
                self.accepted_auth = None
            self._apply_live(cfg['device'])
            self._retire_old_topics()
            Log.info(TAG, 'Config saved.' + (' Restart to apply it all.' if self.restart_required() else ''))
            return True, None

        except Exception as e:
            Log.error(TAG, f'Config save error: {e}')
            return False, str(e)

    def _apply_live(self, dev):
        """Apply the saved settings that don't need a restart."""
        dc = self.device_config
        for key in LIVE_DEVICE_KEYS:
            if key in dev:
                setattr(dc, key, dev[key])
        if Log.level != dc.log_level:
            Log.set_log_level(dc.log_level)

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
