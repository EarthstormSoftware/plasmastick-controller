"""MQTT topics and payloads for reporting state, plus Home Assistant discovery.

Pure functions, so they can be tested off the device. main.py does the
publishing and subscribing.

  /home/device/<id>/control   commands (StateChange / ModeChange), unchanged
  /home/device/<id>/state     retained: {"State", "Mode", "ModeParameters"}
  /home/device/<id>/status    retained: "online", or "offline" (the will)
  /home/device/<id>/ha/state  retained: state in Home Assistant's JSON light schema
  /home/device/<id>/ha/set    Home Assistant JSON light commands
  homeassistant/light/<id>/config   retained discovery config (if enabled)
"""
import ujson

ONLINE = 'online'
OFFLINE = 'offline'


def topics(client_id):
    base = '/home/device/' + client_id
    return {
        'control': base + '/control',
        'state': base + '/state',
        'status': base + '/status',
        'ha_state': base + '/ha/state',
        'ha_set': base + '/ha/set',
        'ha_config': 'homeassistant/light/' + client_id + '/config',
    }


def _rgb(colour):
    digits = colour[1:] if colour.startswith('#') else colour
    return {'r': int(digits[0:2], 16), 'g': int(digits[2:4], 16), 'b': int(digits[4:6], 16)}


def state_payload(report):
    """report: {'state': bool, 'mode': str, 'colour': '#rrggbb' or None}."""
    parameters = {'Colour': report['colour']} if report.get('colour') else {}
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'], 'ModeParameters': parameters})


def ha_state_payload(report):
    payload = {'state': 'ON' if report['state'] else 'OFF', 'color_mode': 'rgb', 'effect': report['mode']}
    if report.get('colour'):
        payload['color'] = _rgb(report['colour'])
    return ujson.dumps(payload)


def ha_discovery_payload(client_id, modes, sw_version=None):
    t = topics(client_id)
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
        'effect': True,
        'effect_list': list(modes),
        'device': device,
    })


def parse_ha_command(payload, modes):
    """Turn a Home Assistant JSON light command into actions, in order:
    ('power', bool) and ('mode', name, parameters or None).
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
    state = payload.get('state')
    if state is not None:
        if state not in ('ON', 'OFF'):
            raise ValueError('state must be ON or OFF')
        actions.append(('power', state == 'ON'))
    if not actions:
        raise ValueError('nothing to do')
    return actions
