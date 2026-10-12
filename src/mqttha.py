"""Home Assistant discovery: the configs that announce the light and its
sensors. Only needed while connecting, so app.py imports it then and lets
it go again.
"""
import ujson
from mqttstate import MIN_MIREDS, MAX_MIREDS, SENSOR_KEYS

# Home Assistant diagnostic sensors, read from the info topic: (key, name, extra config).
SENSORS = (
    ('rssi', 'Wi-Fi signal', {'device_class': 'signal_strength', 'unit_of_measurement': 'dBm',
                              'state_class': 'measurement'}),
    ('uptime_s', 'Uptime', {'device_class': 'duration', 'unit_of_measurement': 's',
                            'state_class': 'total_increasing'}),
    ('mem_free', 'Free memory', {'device_class': 'data_size', 'unit_of_measurement': 'B',
                                 'state_class': 'measurement'}),
    ('ip', 'IP address', {}),
)


def ha_discovery_payload(client_id, t, modes, sw_version=None):
    """t: the device's topics() dict."""
    device = {'identifiers': [client_id], 'name': client_id,
              'manufacturer': 'Pimoroni', 'model': 'Plasma Stick 2040 W'}
    if sw_version:
        device['sw_version'] = sw_version
    return ujson.dumps({
        'name': None,  # the entity takes the device's name
        'unique_id': client_id + '_light',
        'schema': 'json',
        'command_topic': t['ha_set'],
        'state_topic': t['ha_state'],
        'availability_topic': t['status'],
        'supported_color_modes': ['rgb', 'color_temp'],
        'min_mireds': MIN_MIREDS,
        'max_mireds': MAX_MIREDS,
        'brightness': True,
        'brightness_scale': 100,  # percent, as the device uses
        'effect': True,
        'effect_list': list(modes),
        'device': device,
    })


def ha_sensor_discovery_payload(client_id, t, key):
    """Discovery config for one of SENSORS, on the same device as the light."""
    for k, name, extra in SENSORS:
        if k == key:
            config = {
                'name': name,
                'unique_id': client_id + '_' + key,
                'state_topic': t['info'],
                'value_template': '{{ value_json.' + key + ' }}',
                'availability_topic': t['status'],
                'entity_category': 'diagnostic',
                'device': {'identifiers': [client_id]},
            }
            config.update(extra)
            return ujson.dumps(config)
    raise ValueError('unknown sensor')
