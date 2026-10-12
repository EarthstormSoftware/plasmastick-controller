"""MQTT topics and payloads for reporting state, plus Home Assistant discovery.

Pure functions, so they can be tested off the device. main.py does the
publishing and subscribing.

The topics hang off a configurable base (mqtt.topic_base, where {name} is the
device name) and Home Assistant discovery prefix (mqtt.ha_prefix):

  <base>/control      commands (StateChange / ModeChange / BrightnessChange)
  <base>/state        retained: {"State", "Mode", "ModeParameters", "Brightness"}
  <base>/status       retained: "online", or "offline" (the will)
  <base>/ha/state     retained: state in Home Assistant's JSON light schema
  <base>/ha/set       Home Assistant JSON light commands
  <ha_prefix>/light/<name>/config   retained discovery config (if enabled)
"""
import ujson

ONLINE = 'online'
OFFLINE = 'offline'

DEFAULT_TOPIC_BASE = '/home/device/{name}'
DEFAULT_HA_PREFIX = 'homeassistant'
MAX_TOPIC_PREFIX_LEN = 100

# The topics the device publishes retained, so ones it stops using need clearing.
RETAINED = ('state', 'status', 'ha_state', 'ha_config')


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
    return {
        'control': base + '/control',
        'state': base + '/state',
        'status': base + '/status',
        'ha_state': base + '/ha/state',
        'ha_set': base + '/ha/set',
        'ha_config': ha_prefix + '/light/' + client_id + '/config',
    }


def retired_topics(old, new):
    """The retained topics in old (a topics() dict) that new no longer uses."""
    current = new.values()
    return [old[k] for k in RETAINED if old[k] not in current]


def _rgb(colour):
    digits = colour[1:] if colour.startswith('#') else colour
    return {'r': int(digits[0:2], 16), 'g': int(digits[2:4], 16), 'b': int(digits[4:6], 16)}


def state_payload(report):
    """report: {'state': bool, 'mode': str, 'colour': '#rrggbb' or None, 'brightness': 1-100}."""
    parameters = {'Colour': report['colour']} if report.get('colour') else {}
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'], 'ModeParameters': parameters,
                        'Brightness': report['brightness']})


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
