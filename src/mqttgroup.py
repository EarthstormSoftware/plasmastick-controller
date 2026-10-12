"""Groups: devices with the same mqtt.group follow each other through a shared,
retained topic (see mqttstate.topics). Only imported when the device is in a group.
"""
import ujson
from mqttstate import _parameters

# The report keys behind each mode parameter a group passes on (CheerLights
# members fetch their own colour, so only Colour mode's colour goes; Kelvin, a
# colour temperature, is Colour mode's too).
_GROUP_KEYS = (('Colour', 'colour'), ('Kelvin', 'kelvin'), ('Style', 'style'), ('Speed', 'speed'))
_COLOUR_KEYS = ('Colour', 'Kelvin')


def group_payload(report, client_id):
    """The group's new state after a local change: like the state payload, with
    only the parameters that define the mode, and who sent it."""
    return ujson.dumps({'State': report['state'], 'Mode': report['mode'],
                        'ModeParameters': _parameters(report, colour=report['mode'] == 'Colour'),
                        'Brightness': report['brightness'], 'From': client_id})


def group_actions(group, current, follow_brightness):
    """What following the group (from parse_group) changes, given what the device
    shows now (current: {'state', 'mode', 'colour', 'kelvin', 'style', 'speed', 'brightness'}):
    ('power', bool), ('mode', name, parameters or None) and ('brightness', n).
    Only differences, so a device's own message coming back changes nothing."""
    actions = []
    if group['state'] != current['state']:
        actions.append(('power', group['state']))
    wanted = {k: group['parameters'][k] for k, _ in _GROUP_KEYS if group['parameters'].get(k) is not None
              and (k not in _COLOUR_KEYS or group['mode'] == 'Colour')}
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
