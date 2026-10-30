"""
MQTT: the device's state and commands, and Home Assistant discovery, as PlasmaStick's, so home automation sees every
Earthstorm device the same way.

The topics hang off mqtt.topic_base ({name} is the device name) and the discovery prefix, mqtt.ha_prefix:

  <base>/status        retained: online, or offline (the will)
  <base>/info          retained: the health figures from /api/info, every minute
  <base>/log           the device's warnings and errors as it logs them (the last 8 kept while it's offline)
  <base>/state         retained, a product that isn't a light: its state(device), when it calls device.state_changed()
  <base>/set           a product that isn't a light: commands, as a JSON object, for its set(device, command)
  <ha_prefix>/sensor/<name>/<key>/config   retained discovery of the health figures (if it's on)
  <ha_prefix>/<component>/<name>/<key>/config   the same for a product's own entities: its HA_ENTITIES, as
                       (component, key), each described by its ha_entity(device, key, topics)

A light's topics (its state and commands, alerts and groups) are base.lightmqtt's, loaded only for a light. The
product can listen to topics of its own (mqtt_topics(device) and mqtt_message(device, topic, text)).

Retained topics the device stops using (after a topic setting changes) are cleared when it next connects.

The client is the platform's (platform.mqtt_client): base.mqttclient on a Pi, mqtt_as on a Pico.
"""
from base.compat import asyncio, json, release
from base.log import Log
from base.settings import Setting, is_bool, is_text

TAG = 'MQTT'
ONLINE = 'online'

OFFLINE = 'offline'

DEFAULT_TOPIC_BASE = '/home/device/{name}'

DEFAULT_HA_PREFIX = 'homeassistant'

DEFAULT_PORT = 1883

INFO_INTERVAL_S = 60
# The warnings and errors kept for the log topic, and how much of each
LOG_LINES = 8
LOG_LINE_MAX = 160

# The health figures Home Assistant can show (base.discovery describes them), where the platform has them
SENSOR_KEYS = ('uptime_s', 'temperature_c', 'load', 'mem_free', 'rssi', 'ip')


def _prefix_error(value, placeholder):
    if not isinstance(value, str) or not value:
        return "can't be empty"
    if len(value) > 100:
        return 'must be at most 100 characters'
    if '+' in value or '#' in value:
        return "can't contain the wildcards + or #"
    if '\0' in value:
        return "can't contain a null character"
    if value != value.strip():
        return "can't start or end with a space"
    if value.endswith('/'):
        return "can't end with /"
    if '//' in value:
        return "can't contain an empty level (//)"
    rest = value.replace('{name}', '') if placeholder else value
    if '{' in rest or '}' in rest:
        return 'only understands the placeholder {name}' if placeholder else "can't contain { or }"
    return None


def topic_base_error(value):
    return _prefix_error(value, True)


def ha_prefix_error(value):
    return _prefix_error(value, False)


def broker_error(value):
    if not isinstance(value, str) or len(value) > 100 or value != value.strip() or ' ' in value:
        return 'must be a host name or address, optionally with :port'
    try:
        broker(value)
    except ValueError as e:
        return str(e)
    return None


def broker(address):
    """(host, port) from 'host', 'host:port', or '[IPv6]:port'."""
    host, port = address, DEFAULT_PORT
    if address.startswith('['):
        host, _, rest = address[1:].partition(']')
        if rest:
            port = rest[1:]
    elif address.count(':') == 1:
        host, port = address.split(':')
    try:
        port = int(port)
    except ValueError:
        raise ValueError('has a port that isn\'t a number')
    if not 0 < port < 65536:
        raise ValueError('has a port outside 1-65535')
    return host, port


# Changing any of them reconnects with the new ones, through a restart
MQTT_SETTINGS = {
    'mqtt': {
        'enabled': Setting(False, is_bool, restart=True),
        'broker_address': Setting('', broker_error, restart=True),
        'user': Setting('', is_text(64), restart=True),
        # Kept as given (the broker needs it), but never shown
        'password': Setting('', is_text(64, allow_empty=False), restart=True, secret=True),
        'topic_base': Setting(DEFAULT_TOPIC_BASE, topic_base_error, restart=True),
        'ha_discovery': Setting(False, is_bool, restart=True),
        'ha_prefix': Setting(DEFAULT_HA_PREFIX, ha_prefix_error, restart=True),
    },
}


def watch_topic_error(value):
    """For a topic the device subscribes to: wildcards are allowed."""
    if not isinstance(value, str) or len(value) > 200 or '\0' in value or value != value.strip():
        return 'must be a topic, up to 200 characters'
    return None


# A light's group topic (base.lightmqtt), and PlasmaStick's, until it's on App Base
GROUP_PREFIX = 'earthstorm/group/'
OLD_GROUP_PREFIX = 'plasmastick/group/'


def topics(name, base=DEFAULT_TOPIC_BASE, ha_prefix=DEFAULT_HA_PREFIX, light=False, sensors=(), group='',
           product_state=False, entities=()):
    """The device's topics by role. entities: a product's Home Assistant entities, as (component, key)."""
    base = base.replace('{name}', name)
    t = {'status': base + '/status', 'info': base + '/info', 'log': base + '/log'}
    if product_state:
        t.update({'state': base + '/state', 'set': base + '/set'})
    for component, key in entities:
        t['ha_' + key] = ha_prefix + '/' + component + '/' + name + '/' + key + '/config'
    if light:
        t.update({
            'state': base + '/state',
            'control': base + '/control',
            'ha_state': base + '/ha/state',
            'ha_set': base + '/ha/set',
            'ha_light': ha_prefix + '/light/' + name + '/config',
            'alert': base + '/alert',
            'alert_set': base + '/alert/set',
            'alert_state': base + '/alert/state',
            'ha_alerts': ha_prefix + '/switch/' + name + '/alerts/config',
            'ha_alert': ha_prefix + '/button/' + name + '/alert/config',
        })
        if group:
            # Not retained by this light alone (the others use it too), so never cleared
            t['group'] = GROUP_PREFIX + group
            t['group_old'] = OLD_GROUP_PREFIX + group
    for key in sensors:
        t['ha_sensor_' + key] = ha_prefix + '/sensor/' + name + '/' + key + '/config'
    return t


def retained(t):
    """The topics in t that the device publishes retained."""
    return sorted(topic for role, topic in t.items()
                  if role not in ('control', 'ha_set', 'alert', 'alert_set', 'group', 'group_old', 'set', 'log'))


def info_payload(info):
    """The health figures from /api/info that the info topic carries."""
    payload = {'version': info['version']}
    for key in SENSOR_KEYS:
        if key in info:
            payload[key] = info[key]
    return json.dumps(payload)


def parse_set(text):
    """A command on the set topic: a JSON object. Raises ValueError."""
    command = json.loads(text)
    if not isinstance(command, dict) or not command:
        raise ValueError('a command must be a JSON object, such as {"enabled": false}')
    return command


def failure(e):
    """Why connecting failed, for the page."""
    if isinstance(e, asyncio.TimeoutError):
        return "The broker didn't answer in time."
    if isinstance(e, ConnectionRefusedError):
        return 'The broker refused the connection: is it running, and on that port?'
    text = str(e) or e.__class__.__name__
    if 'getaddrinfo' in text or 'Name or service' in text or 'nodename' in text:
        return "The broker's address wasn't found."
    return 'Could not connect: ' + text[:1].upper() + text[1:] + ('' if text.endswith('.') else '.')


class Mqtt:
    """MQTT for any device; base.lightmqtt.LightMqtt adds a light's."""

    def __init__(self, device):
        self.device = device
        s = device.settings
        self.enabled = s.get('mqtt', 'enabled') and bool(s.get('mqtt', 'broker_address'))
        self.connected = False
        self.error = None
        self.name = device.platform.device_name()
        self.ha = s.get('mqtt', 'ha_discovery')
        self.light = device.light
        # The health figures the platform has: as it says (a Pico's Wi-Fi figures are empty until it's connected), or
        # those it gives now
        self.sensors = getattr(device.platform, 'SENSORS', None)
        if self.sensors is None:
            info = device.info()
            self.sensors = tuple(key for key in SENSOR_KEYS if info.get(key) is not None)
        product = device.product
        self.product_state = self.light is None and hasattr(product, 'state')
        self.entities = getattr(product, 'HA_ENTITIES', ())
        self.topics = self._topics()
        self._changed = asyncio.Event()
        # The latest warnings and errors, sent to the log topic (not retained) while connected; kept while it isn't,
        # so the reason for a dropped connection arrives after it. A few lines, cut short: a Pico's heap is small
        self._log, self._logged = [], asyncio.Event()
        if self.enabled:
            Log.sink = self._log_line

    def _topics(self):
        s = self.device.settings
        return topics(self.name, s.get('mqtt', 'topic_base'), s.get('mqtt', 'ha_prefix'), sensors=self.sensors,
                      product_state=self.product_state, entities=self.entities)

    def status(self):
        return {'enabled': self.enabled, 'connected': self.connected, 'error': self.error}

    async def run(self):
        if not self.enabled:
            return
        s = self.device.settings
        host, port = broker(s.get('mqtt', 'broker_address'))
        wait = 1
        while True:
            client = self.device.platform.mqtt_client(
                client_id=self.name, host=host, port=port, user=s.get('mqtt', 'user'),
                password=s.reveal('mqtt', 'password'), will=(self.topics['status'], OFFLINE, True))
            tasks = []
            try:
                await client.connect()
                self.connected, self.error, wait = True, None, 1
                Log.info(TAG, 'Connected to {}:{}'.format(host, port))
                await self._up(client)
                tasks = [asyncio.create_task(task) for task in self._tasks(client)]
                await self._receive(client)
                self.error = 'The connection to the broker dropped.'
            except Exception as e:  # anything a broker, or the network, can do: try again
                self.error = failure(e)
            finally:
                self.connected = False
                for task in tasks:
                    task.cancel()
                client.close()
            Log.warn(TAG, '{} Trying again in {} s'.format(self.error, wait))
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)

    def _tasks(self, client):
        return [self._publish_state(client), self._publish_info(client), self._publish_log(client)]

    def _log_line(self, line):
        self._log.append(line[:LOG_LINE_MAX])
        if len(self._log) > LOG_LINES:
            self._log.pop(0)
        self._logged.set()

    async def _publish_log(self, client):
        try:
            while True:
                await self._logged.wait()
                self._logged.clear()
                while self._log:
                    await client.publish(self.topics['log'], self._log[0])
                    self._log.pop(0)
        except OSError:
            client.close()

    async def _up(self, client):
        t = self.topics
        await client.publish(t['status'], ONLINE, retain=True)
        await self._clear_retired(client)
        # With discovery off, an empty retained config removes any earlier one from Home Assistant. The discovery
        # code is only needed now, so it's let go again afterwards
        from base import discovery
        try:
            await self._up_light(client, discovery)
            if self.product_state:
                await client.subscribe(t['set'])
            product = self.device.product
            for component, key in self.entities:
                await client.publish(t['ha_' + key], discovery.entity(
                    self.name, t, product, key, product.ha_entity(self.device, key, t)) if self.ha else '', retain=True)
            for topic in self._product_topics():
                await client.subscribe(topic)
            await self._up_group(client)
            for key in self.sensors:
                await client.publish(t['ha_sensor_' + key], discovery.sensor(self.name, t, product, key)
                                     if self.ha else '', retain=True)
        finally:
            del discovery
            release('base.discovery')
        await self._send_info(client)
        self._changed.set()  # the state, now it's connected

    async def _up_light(self, client, discovery):
        pass

    async def _up_group(self, client):
        pass

    async def _clear_retired(self, client):
        """Empties retained topics the device used before and doesn't now (a topic setting changed)."""
        path = self.device.platform.state_path('mqtt-topics.json')
        current = retained(self.topics)
        try:
            with open(path) as f:
                before = json.loads(f.read())
        except (OSError, ValueError):
            before = []
        retired = [topic for topic in before if topic not in current]
        for topic in retired:
            await client.publish(topic, '', retain=True)
        if retired:
            Log.info(TAG, 'Cleared {} old retained topic(s)'.format(len(retired)))
        if before != current:
            from base.compat import write_atomically
            write_atomically(path, json.dumps(current))

    def _product_topics(self):
        product = self.device.product
        return product.mqtt_topics(self.device) if hasattr(product, 'mqtt_topics') else []

    async def _send_info(self, client):
        await client.publish(self.topics['info'], info_payload(self.device.info()), retain=True)

    # A publish that fails closes the connection, so run() connects again

    async def _publish_info(self, client):
        try:
            while True:
                await asyncio.sleep(INFO_INTERVAL_S)
                await self._send_info(client)
        except OSError:
            client.close()

    def state_changed(self):
        """The product's state changed (or its settings): publish it."""
        self._changed.set()

    async def _publish_state(self, client):
        if not self.product_state:
            return
        try:
            while True:
                await self._changed.wait()
                self._changed.clear()
                await client.publish(self.topics['state'], json.dumps(self.device.product.state(self.device)),
                                     retain=True)
        except OSError:
            client.close()

    async def _receive(self, client):
        while True:
            message = await client.message()
            if message is None:
                return
            topic, text, retained = message
            Log.debug(TAG, '{}: {}'.format(topic, text))
            try:
                error = self._handle(topic, text, retained)
            except (ValueError, AttributeError) as e:
                error = str(e)
            if error:
                Log.warn(TAG, 'Ignoring a message on {}: {}'.format(topic, error))

    def _handle(self, topic, text, retained):
        """Acts on a message; returns why it couldn't, or None. Raises ValueError for one it can't read."""
        if topic == self.topics.get('set'):
            if retained:  # it would be done again on every connection, undoing later changes
                return 'commands should not be retained'
            return self.device.product.set(self.device, parse_set(text)) if hasattr(self.device.product, 'set') \
                else None
        if hasattr(self.device.product, 'mqtt_message'):
            self.device.product.mqtt_message(self.device, topic, text)
        return None
