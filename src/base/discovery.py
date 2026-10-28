"""
Home Assistant discovery: the configs that announce a device and its entities. Only needed while MQTT connects
(base.mqtt's _up), so it's loaded then and let go again.
"""
from base.compat import json

MANUFACTURER = 'Earthstorm Software'

# Home Assistant gives colour temperatures in mireds (a million over kelvin): 6500 K to 2000 K
MIN_MIREDS, MAX_MIREDS = 153, 500

# The health figures Home Assistant shows as diagnostic sensors, where the platform has them: (key, name, config)
SENSORS = (
    ('uptime_s', 'Uptime', {'device_class': 'duration', 'unit_of_measurement': 's',
                            'state_class': 'total_increasing'}),
    ('temperature_c', 'Temperature', {'device_class': 'temperature', 'unit_of_measurement': '°C',
                                      'state_class': 'measurement'}),
    ('load', 'Load', {'state_class': 'measurement', 'icon': 'mdi:gauge'}),
    ('mem_free', 'Free memory', {'device_class': 'data_size', 'unit_of_measurement': 'B',
                                 'state_class': 'measurement'}),
    ('rssi', 'Wi-Fi signal', {'device_class': 'signal_strength', 'unit_of_measurement': 'dBm',
                              'state_class': 'measurement'}),
    ('ip', 'IP address', {}),
)


def device(name, product):
    d = {'identifiers': [name], 'name': name, 'manufacturer': getattr(product, 'MANUFACTURER', MANUFACTURER),
         'model': getattr(product, 'MODEL', product.NAME), 'sw_version': product.VERSION}
    return d


def entity(name, t, product, key, config):
    """A product's own entity: its config, with what every entity has (read from the state topic unless it says)."""
    full = {'unique_id': name + '_' + key, 'state_topic': t.get('state'), 'availability_topic': t['status'],
            'device': device(name, product)}
    full.update(config)
    return json.dumps(full)


def sensor(name, t, product, key):
    for k, title, extra in SENSORS:
        if k == key:
            config = {
                'name': title,
                'unique_id': name + '_' + key,
                'state_topic': t['info'],
                'value_template': '{{ value_json.' + key + ' }}',
                'availability_topic': t['status'],
                'entity_category': 'diagnostic',
                'device': device(name, product),
            }
            config.update(extra)
            return json.dumps(config)
    raise ValueError('unknown sensor')


# A light's (base.lightmqtt)

def light(name, t, product, modes):
    return json.dumps({
        'name': None,  # the entity takes the device's name
        'unique_id': name + '_light',
        'schema': 'json',
        'command_topic': t['ha_set'],
        'state_topic': t['ha_state'],
        'availability_topic': t['status'],
        'supported_color_modes': ['rgb', 'color_temp'],
        'min_mireds': MIN_MIREDS,
        'max_mireds': MAX_MIREDS,
        'brightness': True,
        'brightness_scale': 100,  # percent, as the device has it
        'effect': True,
        'effect_list': list(modes),
        'device': device(name, product),
    })


def alerts(name, t, product):
    """The switch that turns alerts on and off."""
    return json.dumps({
        'name': 'Alerts', 'unique_id': name + '_alerts', 'icon': 'mdi:alarm-light-outline',
        'command_topic': t['alert_set'], 'payload_on': '{"Enabled": true}', 'payload_off': '{"Enabled": false}',
        'state_topic': t['alert_state'], 'value_template': "{{ 'ON' if value_json.Enabled else 'OFF' }}",
        'state_on': 'ON', 'state_off': 'OFF', 'availability_topic': t['status'], 'device': device(name, product),
    })


def alert(name, t, product):
    """The button that starts an alert (when they're switched on)."""
    return json.dumps({
        'name': 'Alert', 'unique_id': name + '_alert', 'icon': 'mdi:alarm-light', 'command_topic': t['alert'],
        'payload_press': '{}', 'availability_topic': t['status'], 'device': device(name, product),
    })
