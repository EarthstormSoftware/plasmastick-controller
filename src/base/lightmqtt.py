"""
A light's MQTT, next to what every device has (base.mqtt): loaded only for a light, so a sensor doesn't carry it.

  <base>/state         retained: {"State", "Mode", "ModeParameters", "Brightness"}, on every change
  <base>/control       {"MessageType": "StateChange" | "ModeChange" | "BrightnessChange", "Parameters": {...}}
  <base>/ha/state      retained: the state in Home Assistant's JSON light schema
  <base>/ha/set        Home Assistant's JSON light commands
  <base>/alert         starts an alert (if alerts are switched on): empty, or {"Colour", "Seconds", "OnMs", "OffMs",
                       "Brightness"} for other than the alert settings; {"Active": false} ends one
  <base>/alert/set     {"Enabled": true | false} switches alerts on or off
  <base>/alert/state   retained: {"Enabled", "Active"}
  <ha_prefix>/light/<name>/config, and for alerts .../switch/<name>/alerts and .../button/<name>/alert: retained
                       discovery (if it's on)

Groups (mqtt.group): lights with the same group name follow each other through a retained topic,
earthstorm/group/<group>, carrying {"State", "Mode", "ModeParameters", "Brightness", "From"}. Each member publishes its
own changes there (not the ones it makes following the group), and one that starts or joins takes the group's state.
A member follows power, and brightness unless mqtt.group_brightness is off, and the mode only if it has it. Until
PlasmaStick moves to App Base, it uses plasmastick/group/<group>: lights here publish to both and listen to both.

mqtt.alert_topic, if set, is another device's topic to watch: a message on it starts an alert (see watched()), so a
motion sensor can ring the light like a doorbell.
"""
from base.compat import asyncio, json
from base.mqtt import Mqtt, topics, watch_topic_error
from base.settings import Setting, is_bool

# Home Assistant gives colour temperatures in mireds (a million over kelvin): 6500 K to 2000 K
MIN_MIREDS, MAX_MIREDS = 153, 500


# A light's, next to those
LIGHT_MQTT_SETTINGS = {
    'mqtt': {
        # A group to follow ('' for none), and whether to follow its brightness
        'group': Setting('', lambda value: group_error(value), restart=True),
        'group_brightness': Setting(True, is_bool, restart=True),
        # Another device's topic whose messages start an alert ('' for none)
        'alert_topic': Setting('', lambda value: watch_topic_error(value), restart=True),
    },
}

_GROUP_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'

# The mode parameters a group passes on
_GROUP_PARAMETERS = ('Colour', 'Kelvin', 'Speed', 'Style')

GROUP_SETTLE_MS = 300

def group_error(value):
    if not isinstance(value, str) or len(value) > 32 or any(c not in _GROUP_CHARS for c in value):
        return 'must be up to 32 letters, digits, - or _'
    return None



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




def ha_state_payload(state):
    """state: Light.state(). The same in Home Assistant's JSON light schema."""
    parameters = state['ModeParameters']
    payload = {'state': 'ON' if state['State'] else 'OFF', 'color_mode': 'rgb', 'effect': state['Mode'],
               'brightness': state['Brightness']}
    kelvin = parameters.get('Kelvin')
    if kelvin:
        payload['color_mode'] = 'color_temp'
        payload['color_temp'] = (1000000 + kelvin // 2) // kelvin
    colour = parameters.get('Colour')
    if colour:
        digits = colour.lstrip('#')
        payload['color'] = {'r': int(digits[0:2], 16), 'g': int(digits[2:4], 16), 'b': int(digits[4:6], 16)}
    return json.dumps(payload)



def ha_light_payload(name, t, product, modes):
    from base.discovery import device as _device
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
        'device': _device(name, product),
    })



def alert_state_payload(light):
    return json.dumps({'Enabled': light.settings.get('alert', 'enabled'), 'Active': light.alerting})



def ha_alerts_payload(name, t, product):
    from base.discovery import device as _device
    """The switch that turns alerts on and off."""
    return json.dumps({
        'name': 'Alerts', 'unique_id': name + '_alerts', 'icon': 'mdi:alarm-light-outline',
        'command_topic': t['alert_set'], 'payload_on': '{"Enabled": true}', 'payload_off': '{"Enabled": false}',
        'state_topic': t['alert_state'], 'value_template': "{{ 'ON' if value_json.Enabled else 'OFF' }}",
        'state_on': 'ON', 'state_off': 'OFF', 'availability_topic': t['status'], 'device': _device(name, product),
    })



def ha_alert_payload(name, t, product):
    from base.discovery import device as _device
    """The button that starts an alert (when they're switched on)."""
    return json.dumps({
        'name': 'Alert', 'unique_id': name + '_alert', 'icon': 'mdi:alarm-light', 'command_topic': t['alert'],
        'payload_press': '{}', 'availability_topic': t['status'], 'device': _device(name, product),
    })



def parse_alert(text):
    """A message on the alert topic, as keyword arguments for Light.alert, or None to end the alert."""
    if not text.strip():
        return {}
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError('an alert must be empty or a JSON object')
    if payload.get('Active') is False:
        return None
    names = {'Colour': 'colour', 'Seconds': 'seconds', 'OnMs': 'on_ms', 'OffMs': 'off_ms', 'Brightness': 'brightness'}
    return {names[k]: v for k, v in payload.items() if k in names}



_OFF = ('', '0', 'off', 'false', 'no', 'clear', 'idle', 'closed', 'inactive')

def watched(text):
    """Whether a message on the watched topic means "now": a JSON object with "motion" (Pico PIR's state),
    "State" or "state" says so itself; true or ON does; false, OFF, 0 or empty doesn't; any other text does."""
    try:
        payload = json.loads(text)
    except ValueError:
        return text.strip().lower() not in _OFF
    if isinstance(payload, dict):
        for key in ('motion', 'State', 'state', 'occupancy'):
            if key in payload:
                value = payload[key]
                return value.strip().lower() not in _OFF if isinstance(value, str) else bool(value)
        return True
    if isinstance(payload, str):
        return payload.strip().lower() not in _OFF
    return bool(payload)



def topic_matches(pattern, topic):
    """Whether topic is one that the subscription pattern (with + and # wildcards) receives."""
    wanted, parts = pattern.split('/'), topic.split('/')
    for i, level in enumerate(wanted):
        if level == '#':
            return True
        if i >= len(parts) or (level != '+' and level != parts[i]):
            return False
    return len(parts) == len(wanted)



def _number(value, low, high, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError('{} must be {}-{}'.format(what, low, high))
    return value



def parse_control(payload):
    """A control message (PlasmaStick's): StateChange, ModeChange or BrightnessChange. Raises ValueError."""
    if not isinstance(payload, dict):
        raise ValueError('a control message must be a JSON object')
    kind = payload.get('MessageType')
    parameters = payload.get('Parameters') or {}
    if not isinstance(parameters, dict):
        raise ValueError('Parameters must be an object')
    if kind == 'StateChange':
        if not isinstance(parameters.get('State'), bool):
            raise ValueError('State must be true or false')
        return {'on': parameters['State']}
    if kind == 'ModeChange':
        return {'mode': parameters.get('Mode'), 'parameters': parameters.get('ModeParameters')}
    if kind == 'BrightnessChange':
        return {'brightness': parameters.get('Brightness')}
    raise ValueError('unknown MessageType {}'.format(kind))



def parse_ha_command(payload, modes):
    """A Home Assistant JSON light command. Raises ValueError for anything it can't use."""
    if not isinstance(payload, dict):
        raise ValueError('a command must be a JSON object')
    change = {}
    colour, effect = payload.get('color'), payload.get('effect')
    if colour is not None:
        try:
            r, g, b = (int(colour[k]) for k in ('r', 'g', 'b'))
        except (KeyError, TypeError, ValueError):
            raise ValueError('color needs r, g and b')
        if not all(0 <= v <= 255 for v in (r, g, b)):
            raise ValueError('color values must be 0-255')
        change.update(mode='Colour', parameters={'Colour': '#{:02x}{:02x}{:02x}'.format(r, g, b)})
    elif payload.get('color_temp') is not None:
        mireds = _number(payload['color_temp'], 100, 1000, 'color_temp')
        change.update(mode='Colour', parameters={'Kelvin': min(6500, max(2000, int(1000000 / mireds + 0.5)))})
    elif effect is not None:
        if effect not in modes:
            raise ValueError('unknown effect')
        change['mode'] = effect
    if payload.get('brightness') is not None:
        change['brightness'] = max(1, int(_number(payload['brightness'], 0, 100, 'brightness') + 0.5))
    state = payload.get('state')
    if state is not None:
        if state not in ('ON', 'OFF'):
            raise ValueError('state must be ON or OFF')
        change['on'] = state == 'ON'
    if not change:
        raise ValueError('nothing to do')
    if payload.get('transition') is not None:
        change['transition_ms'] = int(_number(payload['transition'], 0, 3600, 'transition') * 1000 + 0.5)
    return change


class LightMqtt(Mqtt):
    def __init__(self, device):
        s = device.settings
        self.group = s.get('mqtt', 'group')
        self.alert_topic = s.get('mqtt', 'alert_topic')
        self._watched_before = False  # whether the watched topic last said "now", so one that stays "now" rings once
        self._group_changed = asyncio.Event()
        super().__init__(device)
        self.light.listeners.append(self._light_changed)

    def _topics(self):
        s = self.device.settings
        return topics(self.name, s.get('mqtt', 'topic_base'), s.get('mqtt', 'ha_prefix'), light=True,
                      sensors=self.sensors, group=self.group, entities=self.entities)

    def _light_changed(self, light):
        self._changed.set()
        if light.local and self.group:
            self._group_changed.set()

    def alert_switched(self):
        """Publishes the alert state again (the switch changed on the page)."""
        self._changed.set()

    def _tasks(self, client):
        return super()._tasks(client) + [self._publish_group(client)]

    async def _up_light(self, client, discovery):
        t = self.topics
        await client.subscribe(t['control'])
        if self.ha:
            await client.subscribe(t['ha_set'])
        await client.subscribe(t['alert'])
        await client.subscribe(t['alert_set'])
        if self.alert_topic:
            await client.subscribe(self.alert_topic)
        product = self.device.product
        for role, payload in (('ha_light', lambda: ha_light_payload(self.name, t, product, self.light.modes)),
                              ('ha_alerts', lambda: ha_alerts_payload(self.name, t, product)),
                              ('ha_alert', lambda: ha_alert_payload(self.name, t, product))):
            await client.publish(t[role], payload() if self.ha else '', retain=True)

    async def _up_group(self, client):
        if self.group:
            # The group's retained state arrives straight away, and the light takes it: PlasmaStick's topic second,
            # as it's the one they use until they're on App Base
            await client.subscribe(self.topics['group'])
            await client.subscribe(self.topics['group_old'])

    async def _publish_group(self, client):
        """Passes changes made here to the group, as its new state: a moment after each, so dragging a slider makes
        one message rather than dozens."""
        if not self.group:
            return
        self._group_changed.clear()  # what happened while disconnected: the group's state wins on joining
        try:
            while True:
                await self._group_changed.wait()
                await asyncio.sleep(GROUP_SETTLE_MS / 1000)
                self._group_changed.clear()
                payload = group_payload(self.light, self.name)
                await client.publish(self.topics['group'], payload, retain=True)
                await client.publish(self.topics['group_old'], payload, retain=True)
        except OSError:
            client.close()

    async def _publish_state(self, client):
        try:
            while True:
                await self._changed.wait()
                self._changed.clear()
                state = self.light.state()
                await client.publish(self.topics['state'], json.dumps(state), retain=True)
                if self.ha:
                    await client.publish(self.topics['ha_state'], ha_state_payload(state), retain=True)
                await client.publish(self.topics['alert_state'], alert_state_payload(self.light), retain=True)
        except OSError:
            client.close()

    def _handle(self, topic, text, retained):
        t, light = self.topics, self.light
        if topic == t.get('control'):
            return light.set(**parse_control(json.loads(text)))
        if topic == t.get('ha_set'):
            return light.set(**parse_ha_command(json.loads(text), light.modes))
        if topic == t.get('alert'):
            if retained:  # left on the broker by someone: not an event now
                return None
            alert = parse_alert(text)
            if alert is None:
                light.stop_alert()
                return None
            return light.alert(**alert)
        if topic == t.get('alert_set'):
            enabled = json.loads(text).get('Enabled')
            if not isinstance(enabled, bool):
                return 'Enabled must be true or false'
            error, changed = self.device.settings.update({'alert': {'enabled': enabled}})
            if not error:
                self.device.applied(changed)
            self._changed.set()  # the switch's state, even if it didn't change
            return error
        if topic in (t.get('group'), t.get('group_old')):
            change = group_change(json.loads(text), light, self.device.settings.get('mqtt', 'group_brightness'))
            return light.set(local=False, **change) if change else None
        if self.alert_topic and topic_matches(self.alert_topic, topic):
            now = watched(text)
            ring = now and not self._watched_before and not retained
            self._watched_before = now
            if ring and self.device.settings.get('alert', 'enabled'):
                return light.alert()
            return None
        return super()._handle(topic, text, retained)
