"""
A light's group (base.lightmqtt; mqtt.group): loaded only when the light is in one, so others don't carry it.
"""
from base.compat import asyncio, json

# The mode parameters a group passes on
_GROUP_PARAMETERS = ('Colour', 'Kelvin', 'Speed', 'Style')

GROUP_SETTLE_MS = 300


def group_payload(light, name):
    """The group's new state after a change here: the light's state, with the parameters that define the mode."""
    state = light.state()
    parameters = {k: v for k, v in state['ModeParameters'].items() if k in _GROUP_PARAMETERS}
    if 'Kelvin' in parameters:
        parameters.pop('Colour', None)  # the temperature says it
    return json.dumps({'State': state['State'], 'Mode': state['Mode'], 'ModeParameters': parameters,
                       'Brightness': state['Brightness'], 'From': name})


def group_change(payload, light, follow_brightness):
    """What following the group's state changes, as keyword arguments for Light.set: only what differs, so a light's
    own message coming back changes nothing, and an animation isn't started again. A mode the light doesn't have is
    left out (it keeps its own). Raises ValueError for a state it can't use."""
    if not isinstance(payload, dict):
        raise ValueError('a group state must be a JSON object')
    state, mode, brightness = payload.get('State'), payload.get('Mode'), payload.get('Brightness')
    parameters = payload.get('ModeParameters') or {}
    if not isinstance(state, bool):
        raise ValueError('State must be true or false')
    if isinstance(brightness, bool) or not isinstance(brightness, int) or not 1 <= brightness <= 100:
        raise ValueError('Brightness must be 1-100')
    if not isinstance(parameters, dict):
        raise ValueError('ModeParameters must be an object')
    change = {}
    if state != light.on:
        change['on'] = state
    if mode in light.modes:
        wanted = {k: v for k, v in parameters.items() if k in _GROUP_PARAMETERS}
        if 'Kelvin' in wanted:
            wanted.pop('Colour', None)
        if light.parameters_error(mode, wanted):
            wanted = {}
        differs = any(str(v).lower() != str(light.parameters.get(k)).lower() for k, v in wanted.items())
        if mode != light.mode or differs:
            change['mode'] = mode
            change['parameters'] = wanted or None
    if follow_brightness and brightness != light.brightness:
        change['brightness'] = brightness
    return change


async def publish(mq, client):
    """Passes changes made here to the group, as its new state: a moment after each, so dragging a slider makes
    one message rather than dozens."""
    mq._group_changed.clear()  # what happened while disconnected: the group's state wins on joining
    try:
        while True:
            await mq._group_changed.wait()
            await asyncio.sleep(GROUP_SETTLE_MS / 1000)
            mq._group_changed.clear()
            payload = group_payload(mq.light, mq.name)
            await client.publish(mq.topics['group'], payload, retain=True)
            await client.publish(mq.topics['group_old'], payload, retain=True)
    except OSError:
        client.close()
