"""
Home Assistant discovery: the configs that announce a device and its entities. Only needed while MQTT connects
(base.mqtt's _up), so it's loaded then and let go again.
"""
from base.compat import json

MANUFACTURER = 'Earthstorm Software'

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
