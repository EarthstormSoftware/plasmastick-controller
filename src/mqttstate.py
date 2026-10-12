"""MQTT topics and payloads for reporting state, plus Home Assistant discovery.

Pure functions, so they can be tested off the device. main.py does the
publishing and subscribing.

The topics hang off a configurable base (mqtt.topic_base, where {name} is the
device name) and Home Assistant discovery prefix (mqtt.ha_prefix):

  <base>/control      commands (StateChange / ModeChange / BrightnessChange)
  <base>/state        retained: {"State", "Mode", "ModeParameters", "Brightness"}
  <base>/status       retained: "online", or "offline" (the will)
  <base>/info         retained: {"version", "uptime_s", "rssi", "ip", "mem_free"}, every minute
  <base>/ha/state     retained: state in Home Assistant's JSON light schema
  <base>/ha/set       Home Assistant JSON light commands
  <ha_prefix>/light/<name>/config   retained discovery config (if enabled)
  <ha_prefix>/sensor/<name>/<key>/config   the same for the info sensors
"""
import ujson

ONLINE = 'online'
OFFLINE = 'offline'

DEFAULT_TOPIC_BASE = '/home/device/{name}'
DEFAULT_HA_PREFIX = 'homeassistant'
MAX_TOPIC_PREFIX_LEN = 100

INFO_INTERVAL_S = 60

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

# The topics the device publishes retained, so ones it stops using need clearing.
RETAINED = ('state', 'status', 'info', 'ha_state', 'ha_config') + tuple('ha_sensor_' + s[0] for s in SENSORS)


def _prefix_error(value, setting, placeholder):
    if not isinstance(value, str) or not value:
        return setting + ' cannot be empty'
    if len(value) > MAX_TOPIC_PREFIX_LEN:
        return setting + ' must be at most ' + str(MAX_TOPIC_PREFIX_LEN) + ' characters'
    if '+' in value or '#' in value:
        return setting + ' cannot contain the wildcards + or #'
    if '\0' in value:
        return setting + ' cannot contain a null character'
    if value != value.strip():
        return setting + ' cannot start or end with a space'
    if value.endswith('/'):
        return setting + ' cannot end with /'
    if '//' in value:
        return setting + ' cannot contain an empty level (//)'
    rest = value.replace('{name}', '') if placeholder else value
    if '{' in rest or '}' in rest:
        return setting + (' only understands the placeholder {name}' if placeholder else ' cannot contain { or }')
    return None


def topic_base_error(base):
    """None if base works as mqtt.topic_base, else why not (starting with the setting's name)."""
    return _prefix_error(base, 'topic_base', True)


def ha_prefix_error(prefix):
    """None if prefix works as mqtt.ha_prefix, else why not."""
    return _prefix_error(prefix, 'ha_prefix', False)


def topics(client_id, base=DEFAULT_TOPIC_BASE, ha_prefix=DEFAULT_HA_PREFIX):
    base = base.replace('{name}', client_id)
    t = {
        'control': base + '/control',
        'state': base + '/state',
        'status': base + '/status',
        'info': base + '/info',
        'ha_state': base + '/ha/state',
        'ha_set': base + '/ha/set',
        'ha_config': ha_prefix + '/light/' + client_id + '/config',
    }
    for key, _, _ in SENSORS:
        t['ha_sensor_' + key] = ha_prefix + '/sensor/' + client_id + '/' + key + '/config'
    return t


def retired_topics(old, new):
    """The retained topics in old (a topics() dict) that new no longer uses."""
    current = new.values()
    return [old[k] for k in RETAINED if old[k] not in current]


def _rgb(colour):
    digits = colour[1:] if colour.startswith('#') else colour
    return {'r': int(digits[0:2], 16), 'g': int(digits[2:4], 16), 'b': int(digits[4:6], 16)}


def state_payload(report):
    """report: {'state': bool, 'mode': str, 'colour': '#rrggbb' or None, 'style': str or None,
    'brightness': 1-100}."""
    parameters = ({'Colour': report['colour']} if report.get('colour') else
                  {'Style': report['style']} if report.get('style') else {})
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'], 'ModeParameters': parameters,
                        'Brightness': report['brightness']})


def info_payload(info):
    """info: DeviceAPI.get_device_info(). The health figures, as /api/info has them."""
    return ujson.dumps({'version': info['version'], 'uptime_s': info['uptime_s'], 'rssi': info['wifi']['rssi'],
                        'ip': info['wifi']['ip'], 'mem_free': info['mem_free']})


def ha_state_payload(report):
    payload = {'state': 'ON' if report['state'] else 'OFF', 'color_mode': 'rgb', 'effect': report['mode'],
               'brightness': report['brightness']}
    if report.get('colour'):
        payload['color'] = _rgb(report['colour'])
    return ujson.dumps(payload)


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
        'supported_color_modes': ['rgb'],
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


def parse_ha_command(payload, modes):
    """Turn a Home Assistant JSON light command into actions, in order:
    ('mode', name, parameters or None), ('brightness', 1-100) and ('power', bool).
    Raises ValueError for anything it can't use."""
    if not isinstance(payload, dict):
        raise ValueError('command must be a JSON object')
    actions = []
    colour = payload.get('color')
    effect = payload.get('effect')
    if colour is not None:
        try:
            r, g, b = (int(colour[k]) for k in ('r', 'g', 'b'))
        except (KeyError, TypeError, ValueError):
            raise ValueError('color needs r, g and b')
        if not all(0 <= v <= 255 for v in (r, g, b)):
            raise ValueError('color values must be 0-255')
        actions.append(('mode', 'Colour', {'Colour': '#{:02x}{:02x}{:02x}'.format(r, g, b)}))
    elif effect is not None:
        if effect not in modes:
            raise ValueError('unknown effect')
        actions.append(('mode', effect, None))
    brightness = payload.get('brightness')
    if brightness is not None:
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)) or not 0 <= brightness <= 100:
            raise ValueError('brightness must be 0-100')
        actions.append(('brightness', max(1, int(brightness + 0.5))))
    state = payload.get('state')
    if state is not None:
        if state not in ('ON', 'OFF'):
            raise ValueError('state must be ON or OFF')
        actions.append(('power', state == 'ON'))
    if not actions:
        raise ValueError('nothing to do')
    return actions
