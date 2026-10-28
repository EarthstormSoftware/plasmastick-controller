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

Groups (mqtt.group; base.lightgroup, loaded only for a light in one): lights with the same group name follow each other through a retained topic,
earthstorm/group/<group>, carrying {"State", "Mode", "ModeParameters", "Brightness", "From"}. Each member publishes its
own changes there (not the ones it makes following the group), and one that starts or joins takes the group's state.
A member follows power, and brightness unless mqtt.group_brightness is off, and the mode only if it has it. Until
PlasmaStick moves to App Base, it uses plasmastick/group/<group>: lights here publish to both and listen to both.

mqtt.alert_topic, if set, is another device's topic to watch: a message on it starts an alert (see
base.lightwatch, loaded only then), so a motion sensor can ring the light like a doorbell. The discovery configs
are base.discovery's, loaded only while connecting.
"""
from base.compat import asyncio, json
from base.mqtt import Mqtt, topics, watch_topic_error
from base.settings import Setting, is_bool

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

def group_error(value):
    if not isinstance(value, str) or len(value) > 32 or any(c not in _GROUP_CHARS for c in value):
        return 'must be up to 32 letters, digits, - or _'
    return None


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


def alert_state_payload(light):
    return json.dumps({'Enabled': light.settings.get('alert', 'enabled'), 'Active': light.alerting})


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
        # Each loaded only when it's used: on a Pico every module kept costs heap
        self._group = self._watch = None
        if self.group:
            from base import lightgroup
            self._group = lightgroup
            self._group_changed = asyncio.Event()
        if self.alert_topic:
            from base import lightwatch
            self._watch = lightwatch
        super().__init__(device)
        self.light.listeners.append(self._light_changed)

    def _topics(self):
        s = self.device.settings
        return topics(self.name, s.get('mqtt', 'topic_base'), s.get('mqtt', 'ha_prefix'), light=True,
                      sensors=self.sensors, group=self.group, entities=self.entities)

    def _light_changed(self, light):
        self._changed.set()
        if light.local and self._group:
            self._group_changed.set()

    def alert_switched(self):
        """Publishes the alert state again (the switch changed on the page)."""
        self._changed.set()

    def _tasks(self, client):
        tasks = super()._tasks(client)
        if self._group:
            tasks.append(self._group.publish(self, client))
        return tasks

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
        for role, payload in (('ha_light', lambda: discovery.light(self.name, t, product, self.light.modes)),
                              ('ha_alerts', lambda: discovery.alerts(self.name, t, product)),
                              ('ha_alert', lambda: discovery.alert(self.name, t, product))):
            await client.publish(t[role], payload() if self.ha else '', retain=True)

    async def _up_group(self, client):
        if self.group:
            # The group's retained state arrives straight away, and the light takes it: PlasmaStick's topic second,
            # as it's the one they use until they're on App Base
            await client.subscribe(self.topics['group'])
            await client.subscribe(self.topics['group_old'])

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
        if self._group and topic in (t.get('group'), t.get('group_old')):
            change = self._group.group_change(json.loads(text), light,
                                              self.device.settings.get('mqtt', 'group_brightness'))
            return light.set(local=False, **change) if change else None
        if self._watch and self._watch.topic_matches(self.alert_topic, topic):
            now = self._watch.watched(text)
            ring = now and not self._watched_before and not retained
            self._watched_before = now
            if ring and self.device.settings.get('alert', 'enabled'):
                return light.alert()
            return None
        return super()._handle(topic, text, retained)
