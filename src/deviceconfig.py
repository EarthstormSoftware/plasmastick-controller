from log import Log
import os
import ujson
import machine
from wificrypto import decrypt_password
from deviceidentity import sanitize_device_name

TAG = "CFG"

_DEFAULT_CONFIG = {
    "device": {
        "device_name": "PicoLights-UNKNOWN",
        "device_led_count": 10,
        "device_led_rgbw": False,
        "default_mode": "Colour",
        "mode_parameters": {"Colour": "#ffffff"},
        "default_state": False,
        "cheerlights_url": "https://api.thingspeak.com/channels/1417/field/2/last.json",
        "cheerlights_update_interval": 60,
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
        "password": "",
        "clean": True,
        "queue_len": 1,
    },
}


DEVICE_NAME_FILE = "device_name.json"
# Written by wlan.py to ask for setup mode on the next boot; removed when read.
SETUP_REQUEST_FILE = "setup_request"


def _redact_passwords(configdata):
    """Copy of the config with any password values masked, for logging."""
    safe = {}
    for section, values in configdata.items():
        if isinstance(values, dict):
            values = {k: ('***' if 'password' in k and v else v) for k, v in values.items()}
        safe[section] = values
    return safe


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
        configured_name = configdata["device"].get("device_name", "")
        self.device_name = sanitize_device_name(_load_renamed_device_name() or provisioned_name or configured_name)
        self.device_led_count = configdata["device"]["device_led_count"]
        self.device_led_rgbw = configdata["device"]["device_led_rgbw"]
        self.default_mode = configdata["device"]["default_mode"]
        self.mode_parameters = configdata["device"]["mode_parameters"]
        self.default_state = configdata["device"]["default_state"]                
        self.cheerlights_url = configdata["device"]["cheerlights_url"]
        self.cheerlights_update_interval = configdata["device"]["cheerlights_update_interval"]

        self.wlanconfig = WlanConfig(configdata["wlan"])
        self.mqttconfig = MQTTConfig(self.device_name, configdata["mqtt"])
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
            Log.info(TAG, _redact_passwords(configdata))
        except OSError as e:
            Log.error(TAG, f'Failed to read config.json: {e}')
            Log.warn(TAG, 'Using built-in defaults.')
            configdata = _DEFAULT_CONFIG
        except ValueError as e:
            Log.error(TAG, f'Invalid JSON in config.json: {e}')
            Log.warn(TAG, 'Using built-in defaults.')
            configdata = _DEFAULT_CONFIG

        return DeviceConfig(configdata)
    
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
        self.password = mqttconfig.get("password", "")
        self.clean = mqttconfig.get("clean", True)
        self.queue_len = mqttconfig.get("queue_len", 1)

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
        return cfg

