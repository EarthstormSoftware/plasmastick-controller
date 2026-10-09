from log import Log
import os
import ujson
import machine
from wificrypto import encrypt_password, decrypt_password
from passwords import hash_password
from deviceidentity import sanitize_device_name

TAG = "CFG"

# A warm white (about 3000 K), the colour until one is chosen.
DEFAULT_COLOUR = "#ffb46b"

_DEFAULT_CONFIG = {
    "device": {
        "device_name": "",  # empty: generated from the board's unique id
        "device_led_count": 10,
        "device_led_rgbw": False,
        "default_mode": "Colour",
        "mode_parameters": {"Colour": DEFAULT_COLOUR},
        "default_state": False,
        "default_brightness": 66,  # percent, 1-100
        # http, as in config.example.json: a TLS handshake holds the CPU for about a second.
        "cheerlights_url": "http://api.thingspeak.com/channels/1417/field/2/last.json",
        "cheerlights_update_interval": 60,
        # 0 = warnings and errors, 1 = also info, 2 = also debug
        "log_level": 1,
    },
    "wlan": {
        "ssid": "",
        "password": "",
        "connect_timeout": 30,
        "reconnect_wait": 30,
        "max_retries": 0,
    },
    "mqtt": {
        "enabled": True,
        "broker_address": "",
        "user": "",
        # The password is kept encrypted, as password_iv and password_enc.
        "clean": True,
        "queue_len": 1,
        # Announce the device to Home Assistant over MQTT discovery.
        "ha_discovery": False,
    },
    "web": {
        # A hash of the password (see passwords.py). Empty: the web UI and API
        # are open to anyone on the network.
        "password_hash": "",
    },
}


DEVICE_NAME_FILE = "device_name.json"


def is_valid_brightness(value):
    """True for a whole-number brightness percentage, 1-100."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100


# Written by wlan.py to ask for setup mode on the next boot; removed when read.
SETUP_REQUEST_FILE = "setup_request"
PROVISIONING_FILE = "provisioning.json"


def _redact_passwords(configdata):
    """Copy of the config with any password values masked, for logging."""
    safe = {}
    for section, values in configdata.items():
        if isinstance(values, dict):
            values = {k: ('***' if 'password' in k and v else v) for k, v in values.items()}
        safe[section] = values
    return safe


def _secure_passwords(configdata):
    """Convert plain-text web and MQTT passwords (from older versions, or a
    hand-edited file) to their stored forms: the web password to a hash, the
    MQTT password encrypted. Changes configdata; returns True if it changed."""
    changed = False
    web = configdata.get("web")
    if isinstance(web, dict) and web.get("password"):
        web["password_hash"] = hash_password(web.pop("password"))
        changed = True
    elif isinstance(web, dict) and "password" in web:
        del web["password"]
        changed = True
    mqtt = configdata.get("mqtt")
    if isinstance(mqtt, dict) and "password" in mqtt:
        password = mqtt.pop("password")
        if password:
            mqtt["password_iv"], mqtt["password_enc"] = encrypt_password(password)
        changed = True
    return changed


def _merge_with_defaults(configdata):
    """Fill in anything config.json leaves out from _DEFAULT_CONFIG, so an older
    or hand-edited file still boots. Logs the keys that were missing."""
    merged = {}
    missing = []
    for section, defaults in _DEFAULT_CONFIG.items():
        values = configdata.get(section)
        if not isinstance(values, dict):
            values = {}
        merged[section] = dict(defaults)
        merged[section].update(values)
        missing += [f'{section}.{key}' for key in defaults if key not in values]
    for section, values in configdata.items():
        if section not in merged:
            merged[section] = values
    if missing:
        Log.info(TAG, f'config.json has no {", ".join(missing)}; using defaults')
    return merged


def _load_renamed_device_name():
    """Return the name set from the web UI/API (device_name.json), if any."""
    try:
        with open(DEVICE_NAME_FILE) as fp:
            return ujson.load(fp).get('name')
    except (OSError, ValueError):
        return None


def _load_provisioning_data():
    """Try to load provisioning.json with WiFi credentials and optional device_name."""
    try:
        with open("provisioning.json") as fp:
            data = ujson.load(fp)
        if 'password_iv' in data and 'password_enc' in data:
            # Encrypted format (current)
            data['password'] = decrypt_password(data['password_iv'], data['password_enc'])
        elif 'password' not in data:
            return None
        # else: plaintext legacy format — use as-is
        return data
    except OSError:
        return None


class DeviceConfig:
    
    def __init__(self, configdata):
        # Device name priority: renamed in the web UI/API, then set during
        # provisioning, then config.json, then generated from the board's unique id.
        # It is used as the MQTT client id and the network hostname.
        provisioning = _load_provisioning_data()
        provisioned_name = provisioning.get('device_name') if provisioning else None
        # The merged config, which the API saves changes on top of.
        self.configdata = configdata
        configured_name = configdata["device"]["device_name"]
        self.device_name = sanitize_device_name(_load_renamed_device_name() or provisioned_name or configured_name)
        self.device_led_count = configdata["device"]["device_led_count"]
        self.device_led_rgbw = configdata["device"]["device_led_rgbw"]
        self.default_mode = configdata["device"]["default_mode"]
        self.mode_parameters = configdata["device"]["mode_parameters"]
        self.default_state = configdata["device"]["default_state"]                
        self.cheerlights_url = configdata["device"]["cheerlights_url"]
        self.cheerlights_update_interval = configdata["device"]["cheerlights_update_interval"]
        self.log_level = configdata["device"]["log_level"]
        if self.log_level not in (0, 1, 2):
            Log.warn(TAG, f'Ignoring log_level {self.log_level!r}; it must be 0, 1 or 2')
            self.log_level = 1
        self.default_brightness = configdata["device"]["default_brightness"]
        if not is_valid_brightness(self.default_brightness):
            Log.warn(TAG, f'Ignoring default_brightness {self.default_brightness!r}; it must be 1-100')
            self.default_brightness = _DEFAULT_CONFIG["device"]["default_brightness"]

        self.wlanconfig = WlanConfig(configdata["wlan"])
        self.mqttconfig = MQTTConfig(self.device_name, configdata["mqtt"])
        self.web_password_hash = configdata["web"].get("password_hash", "")
        Log.info(TAG, f'Using device name: {self.device_name}')
        
    def get_wlanconfig(self):
        return self.wlanconfig
    
    def get_mqttconfig(self):
        return self.mqttconfig

    def get_mqtt_as_config(self):
        return self.mqttconfig.to_mqtt_as_config()

    def is_mqtt_enabled(self):
        return self.mqttconfig.is_enabled()
    
    def get_control_topic(self):
        return '/home/device/' + self.mqttconfig.client_id + '/control'

    @staticmethod
    def provisioning_needed():
        """Check if WiFi provisioning is needed (no provisioning.json with valid credentials)."""
        try:
            with open("provisioning.json") as fp:
                data = ujson.load(fp)
                has_ssid = 'ssid' in data
                has_password = 'password' in data or ('password_iv' in data and 'password_enc' in data)
                if has_ssid and has_password:
                    Log.info(TAG, 'Provisioning config found.')
                    return False
        except OSError:
            pass
        Log.info(TAG, 'No valid provisioning found. AP mode provisioning needed.')
        return True

    @staticmethod
    def request_setup_mode():
        """Ask for setup mode on the next boot, keeping the saved credentials."""
        with open(SETUP_REQUEST_FILE, 'w') as fp:
            fp.write('1')

    @staticmethod
    def take_setup_request():
        """Return True (once) if setup mode was requested for this boot."""
        try:
            os.remove(SETUP_REQUEST_FILE)
            return True
        except OSError:
            return False

    @staticmethod
    def loadConfigFromFile():
        Log.info(TAG,'Loading configuration from file')
        try:
            with open("config.json") as fp:
                configdata = ujson.load(fp)
            if not isinstance(configdata, dict):
                raise ValueError('top level is not an object')
            Log.info(TAG, _redact_passwords(configdata))
        except OSError as e:
            Log.error(TAG, f'Failed to read config.json: {e}')
            Log.warn(TAG, 'Using built-in defaults.')
            configdata = {}
        except ValueError as e:
            Log.error(TAG, f'Invalid JSON in config.json: {e}')
            Log.warn(TAG, 'Using built-in defaults.')
            configdata = {}

        if _secure_passwords(configdata):
            try:
                DeviceConfig.saveConfigToFile(configdata)
                Log.info(TAG, 'Stored passwords converted: web hashed, MQTT encrypted')
            except OSError as e:
                Log.warn(TAG, f'Could not save the converted passwords: {e}')

        return DeviceConfig(_merge_with_defaults(configdata))
    
    @staticmethod
    def clear_web_password():
        """Remove the web password from config.json. Setup mode does this, so
        a forgotten password can be recovered with physical access."""
        try:
            with open("config.json") as fp:
                configdata = ujson.load(fp)
            web = configdata.get("web", {})
            if web.get("password") or web.get("password_hash"):
                web.pop("password", None)
                web["password_hash"] = ""
                DeviceConfig.saveConfigToFile(configdata)
                Log.info(TAG, 'Web password cleared')
        except (OSError, ValueError, AttributeError) as e:
            Log.warn(TAG, f'Could not clear the web password: {e}')

    @staticmethod
    def reset(keep_wifi):
        """Put the settings back to defaults, keeping the LED hardware (count and
        type: they describe the strip, not a preference). With keep_wifi False
        this also forgets the Wi-Fi network, so the next boot starts setup mode."""
        try:
            with open("config.json") as fp:
                old = ujson.load(fp)
        except (OSError, ValueError):
            old = {}
        device = old.get("device") if isinstance(old.get("device"), dict) else {}
        configdata = {"device": {k: device[k] for k in ("device_led_count", "device_led_rgbw") if k in device}}
        wlan = old.get("wlan") if isinstance(old.get("wlan"), dict) else {}
        if keep_wifi and wlan.get("ssid"):
            # Older setups keep the Wi-Fi network in config.json, not provisioning.json.
            configdata["wlan"] = {"ssid": wlan["ssid"], "password": wlan.get("password", "")}
        DeviceConfig.saveConfigToFile(configdata)
        removed = [DEVICE_NAME_FILE] + ([] if keep_wifi else [PROVISIONING_FILE, SETUP_REQUEST_FILE])
        for name in removed:
            try:
                os.remove(name)
            except OSError:
                pass
        Log.warn(TAG, 'Settings reset to defaults' + ('' if keep_wifi else ', Wi-Fi forgotten'))

    @staticmethod
    def saveConfigToFile(configdata):
        Log.info(TAG,'Saving configuration to file')
        with open('config.json', 'w') as f:
            ujson.dump(configdata, f)    
                
    
class WlanConfig:
    def __init__(self, wlanconfig):
        # Check provisioning file first, fall back to config dict
        provisioning = self._load_provisioning()
        if provisioning:
            self.ssid = provisioning.get('ssid', wlanconfig.get('ssid', ''))
            self.password = provisioning.get('password', wlanconfig.get('password', ''))
            Log.info(TAG, f'Using provisioned SSID: {self.ssid}')
        else:
            self.ssid = wlanconfig["ssid"]
            self.password = wlanconfig["password"]
            Log.info(TAG, f'Using config.json SSID: {self.ssid}')
        
        self.connect_timeout = wlanconfig["connect_timeout"]
        self.reconnect_wait = wlanconfig["reconnect_wait"]
        self.max_retries = wlanconfig["max_retries"]
    
    @staticmethod
    def _load_provisioning():
        """Try to load provisioning.json with WiFi credentials."""
        return _load_provisioning_data()


class MQTTConfig:
    def __init__(self, device_name, mqttconfig):
        self.enabled = mqttconfig.get("enabled", True)
        self.client_id = device_name
        self.broker_address = mqttconfig.get("broker_address", "")
        self.user = mqttconfig.get("user", "")
        if mqttconfig.get("password_enc"):
            try:
                self.password = decrypt_password(mqttconfig["password_iv"], mqttconfig["password_enc"])
            except Exception as e:  # a different board, or a damaged file
                Log.warn(TAG, f'Could not decrypt the MQTT password: {e!r}')
                self.password = ""
        else:
            self.password = mqttconfig.get("password", "")
        self.clean = mqttconfig.get("clean", True)
        self.queue_len = mqttconfig.get("queue_len", 1)
        self.ha_discovery = bool(mqttconfig.get("ha_discovery", False))

    def is_enabled(self):
        return bool(self.enabled) and bool(self.broker_address)

    def to_mqtt_as_config(self):
        from mqtt_as import config as mqtt_config  # only loaded when MQTT is used
        cfg = mqtt_config.copy()
        cfg["client_id"] = self.client_id
        cfg["server"] = self.broker_address
        cfg["user"] = self.user
        cfg["password"] = self.password
        cfg["clean"] = self.clean
        cfg["queue_len"] = self.queue_len
        # The broker publishes this if the device drops off without saying goodbye.
        from mqttstate import topics, OFFLINE
        cfg["will"] = (topics(self.client_id)['status'], OFFLINE, True, 0)
        return cfg

