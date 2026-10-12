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
  plasmastick/group/<group>   retained: the group's state, if the device is in a
                      group (mqtt.group): every member follows it and publishes
                      its own local changes to it
"""
import ujson

ONLINE = 'online'
OFFLINE = 'offline'

DEFAULT_TOPIC_BASE = '/home/device/{name}'
# Shared by every member of a group, whatever their own topic base.
GROUP_PREFIX = 'plasmastick/group/'
_GROUP_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'
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


def group_error(group):
    """None if group works as mqtt.group ('' for none), else why not."""
    if not isinstance(group, str) or len(group) > 32 or any(c not in _GROUP_CHARS for c in group):
        return 'group must be up to 32 letters, digits, - or _'
    return None


def topics(client_id, base=DEFAULT_TOPIC_BASE, ha_prefix=DEFAULT_HA_PREFIX, group=''):
    """The device's topics. 'group' is there only when the device is in a group;
    it isn't in RETAINED, as other members still use it after this one leaves."""
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
    if group:
        t['group'] = GROUP_PREFIX + group
    return t


def retired_topics(old, new):
    """The retained topics in old (a topics() dict) that new no longer uses."""
    current = new.values()
    return [old[k] for k in RETAINED if old[k] not in current]


def _rgb(colour):
    digits = colour[1:] if colour.startswith('#') else colour
    return {'r': int(digits[0:2], 16), 'g': int(digits[2:4], 16), 'b': int(digits[4:6], 16)}


def _parameters(report, colour=True):
    """ModeParameters for a report: the colour showing, the Christmas style, the speed."""
    parameters = {}
    if colour and report.get('colour'):
        parameters['Colour'] = report['colour']
    if report.get('style'):
        parameters['Style'] = report['style']
    if report.get('speed'):
        parameters['Speed'] = report['speed']
    if report.get('kelvin'):
        parameters['Kelvin'] = report['kelvin']
    return parameters


def state_payload(report):
    """report: {'state': bool, 'mode': str, 'colour': '#rrggbb' or None, 'style': str or None,
    'speed': 1-10 or None, 'brightness': 1-100}."""
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'], 'ModeParameters': _parameters(report),
                        'Brightness': report['brightness']})


def info_payload(info):
    """info: DeviceAPI.get_device_info(). The health figures, as /api/info has them."""
    return ujson.dumps({'version': info['version'], 'uptime_s': info['uptime_s'], 'rssi': info['wifi']['rssi'],
                        'ip': info['wifi']['ip'], 'mem_free': info['mem_free']})


# The report keys behind each mode parameter a group passes on (CheerLights
# members fetch their own colour, so only Colour mode's colour goes).
_GROUP_KEYS = (('Colour', 'colour'), ('Style', 'style'), ('Speed', 'speed'))


def group_payload(report, client_id):
    """The group's new state after a local change: like the state payload, with
    only the parameters that define the mode, and who sent it."""
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'],
                        'ModeParameters': _parameters(report, colour=report['mode'] == 'Colour'),
                        'Brightness': report['brightness'], 'From': client_id})


def group_actions(group, current, follow_brightness):
    """What following the group (from parse_group) changes, given what the device
    shows now (current: {'state', 'mode', 'colour', 'style', 'speed', 'brightness'}):
    ('power', bool), ('mode', name, parameters or None) and ('brightness', n).
    Only differences, so a device's own message coming back changes nothing."""
    actions = []
    if group['state'] != current['state']:
        actions.append(('power', group['state']))
    wanted = {k: group['parameters'][k] for k, _ in _GROUP_KEYS if group['parameters'].get(k) is not None
              and (k != 'Colour' or group['mode'] == 'Colour')}
    differs = any(str(v).lower() != str(current.get(r) or '').lower() for k, r in _GROUP_KEYS
                  for v in [wanted.get(k)] if v is not None)
    if group['mode'] != current['mode'] or differs:
        actions.append(('mode', group['mode'], wanted or None))
    if follow_brightness and group['brightness'] != current['brightness']:
        actions.append(('brightness', group['brightness']))
    return actions


def parse_group(payload, modes, mode_parameters_error):
    """A group message as {'state', 'mode', 'parameters', 'brightness'}; raises
    ValueError if any part is unusable (so nothing half-applies)."""
    if not isinstance(payload, dict):
        raise ValueError('group state must be a JSON object')
    state, mode = payload.get('State'), payload.get('Mode')
    parameters, brightness = payload.get('ModeParameters') or {}, payload.get('Brightness')
    if not isinstance(state, bool):
        raise ValueError('State must be true or false')
    if mode not in modes:
        raise ValueError('unknown Mode')
    error = mode_parameters_error(mode, parameters)
    if error:
        raise ValueError(error)
    if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 100:
        raise ValueError('Brightness must be 1-100')
    return {'state': state, 'mode': mode, 'parameters': parameters, 'brightness': brightness}


# Home Assistant gives colour temperatures in mireds (a million over kelvin).
MIN_MIREDS, MAX_MIREDS = 153, 500  # 6500 K to 2000 K


def ha_state_payload(report):
    payload = {'state': 'ON' if report['state'] else 'OFF', 'color_mode': 'rgb', 'effect': report['mode'],
               'brightness': report['brightness']}
    if report.get('kelvin'):
        payload['color_mode'] = 'color_temp'
        payload['color_temp'] = (1000000 + report['kelvin'] // 2) // report['kelvin']
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


def parse_ha_command(payload, modes):
    """Turn a Home Assistant JSON light command into actions, in order:
    ('transition', ms) if it asks for one, ('mode', name, parameters or None),
    ('brightness', 1-100) and ('power', bool).
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
    elif payload.get('color_temp') is not None:
        mireds = payload['color_temp']
        if isinstance(mireds, bool) or not isinstance(mireds, (int, float)) or not 100 <= mireds <= 1000:
            raise ValueError('color_temp must be 100-1000 mireds')
        kelvin = min(6500, max(2000, int(1000000 / mireds + 0.5)))
        actions.append(('mode', 'Colour', {'Kelvin': kelvin}))
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
    transition = payload.get('transition')
    if transition is not None:
        if isinstance(transition, bool) or not isinstance(transition, (int, float)) or not 0 <= transition <= 3600:
            raise ValueError('transition must be 0-3600 seconds')
        actions.insert(0, ('transition', int(transition * 1000 + 0.5)))
    return actions
