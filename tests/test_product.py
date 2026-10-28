"""PlasmaStick on App Base: the same topics, Home Assistant light and states as 1.4 (ha_v1.json is what 1.4.2
published for a device called pumpkin in the group porch), so Home Assistant and groups carry on; config.json in
1.4's layout; and the button."""
import json
import os

import product
from base import discovery, lightmqtt, mqtt, pico
from base.light import Light, LIGHT_SETTINGS
from base.schedule import schedule_settings
from base.settings import Settings, BASE_SETTINGS, merge

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ('Colour',) + product.MODE_ORDER


def v1():
    with open(os.path.join(HERE, 'ha_v1.json'), encoding='utf-8') as f:
        return json.load(f)


def topics():
    return mqtt.topics('pumpkin', light=True, sensors=pico.SENSORS, group='porch')


def test_every_topic_1_4_used_is_still_used():
    assert set(v1()['topics'].values()) <= set(topics().values())
    assert topics()['group_old'] == v1()['topics']['group']  # its group topic, still published and followed


def test_the_light_entity_is_1_4s():
    config = json.loads(lightmqtt.ha_light_payload('pumpkin', topics(), product, MODES))
    old = v1()['ha_light']
    config['device']['sw_version'] = old['device']['sw_version']
    assert config == old


def test_the_sensor_entities_keep_their_ids_and_topics():
    t = topics()
    for key in pico.SENSORS:
        config, old = json.loads(discovery.sensor('pumpkin', t, product, key)), v1()['ha_sensor_' + key]
        # 1.4 gave these only the device's identifiers; the base gives the whole device, the same one
        assert config.pop('device')['identifiers'] == old.pop('device')['identifiers']
        assert config == old, key


class Output:
    count = 10
    strip = None

    def paint(self, colour, level):
        pass

    def clear(self):
        pass


def light(tmp_path):
    s = Settings(str(tmp_path / 'config.json'), merge(LIGHT_SETTINGS, schedule_settings(MODES), product.SETTINGS))
    return Light(Output(), s, product.LIGHT_MODES, product, product.MIN_LEVEL)


def test_the_states_are_1_4s(tmp_path):
    lt = light(tmp_path)
    for name, change in (('colour', {'on': True, 'mode': 'Colour', 'parameters': {'Colour': '#ff8000'}, 'brightness': 40}),
                         ('kelvin', {'on': True, 'mode': 'Colour', 'parameters': {'Kelvin': 2700}, 'brightness': 40}),
                         ('rainbows', {'on': False, 'mode': 'Rainbows', 'brightness': 66})):
        assert lt.set(**change) is None
        state = lt.state()
        assert state == v1()['state_' + name], name
        assert json.loads(lightmqtt.ha_state_payload(state)) == v1()['ha_state_' + name], name


V1_CONFIG = {
    'device': {'device_name': '', 'device_led_count': 49, 'device_led_rgbw': False, 'default_mode': 'Rainbows',
               'mode_parameters': {'Colour': '#FFB46B'}, 'default_state': True, 'default_brightness': 40,
               'led_layout': 'vertical', 'led_reverse': True, 'christmas_style': 'Fade', 'transition_ms': 800,
               'effect_speed': 7, 'latitude': 51.5, 'longitude': -0.1,
               'schedule': [{'days': [0, 1, 2, 3, 4], 'at': 'sunset', 'offset': -15, 'action': 'on',
                             'mode': 'Christmas', 'brightness': 60}],
               'cheerlights_url': product.CHEERLIGHTS_URL, 'cheerlights_update_interval': 120},
    'mqtt': {'enabled': True, 'group': 'porch', 'group_brightness': False},
}


def schema():
    return merge(BASE_SETTINGS, mqtt.MQTT_SETTINGS, LIGHT_SETTINGS, schedule_settings(MODES),
                 lightmqtt.LIGHT_MQTT_SETTINGS, product.SETTINGS)


def test_a_1_4_config_is_read_and_kept_in_its_layout(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(V1_CONFIG))
    s = Settings(str(path), schema(), codec=product.Codec)
    assert s.get('device', 'default_mode') == 'Rainbows' and s.get('device', 'default_colour') == '#FFB46B'
    assert s.get('schedule', 'events') == V1_CONFIG['device']['schedule']
    assert (s.get('schedule', 'latitude'), s.get('schedule', 'longitude')) == (51.5, -0.1)
    assert s.get('device', 'led_layout') == 'vertical' and s.get('mqtt', 'group_brightness') is False
    s.update({'device': {'default_brightness': 50}})
    saved = json.loads(path.read_text())
    assert 'schedule' not in saved  # back where 1.4 keeps it
    assert saved['device']['schedule'] == V1_CONFIG['device']['schedule']
    assert saved['device']['latitude'] == 51.5 and saved['device']['mode_parameters'] == {'Colour': '#FFB46B'}
    assert saved['device']['default_brightness'] == 50


def test_the_defaults_are_1_4s(tmp_path):
    s = Settings(str(tmp_path / 'config.json'), schema(), codec=product.Codec)
    assert s.get('device', 'default_state') is False and s.get('device', 'device_led_count') == 10
    assert s.get('device', 'cheerlights_update_interval') == 60


def test_the_button_short_long_and_debounced():
    import button
    b = button.Button.__new__(button.Button)
    b.pressed, b.pressed_at, b.long_done, b.last_raw, b.last_edge = False, 0, False, False, 0
    events = []
    for ms, raw in ((0, True), (10, False), (20, True), (100, True), (300, False), (400, False)):
        events.append(b.update(raw, ms))
    assert events.count('short') == 1 and 'long' not in events
    events = [b.update(raw, ms) for ms, raw in ((1000, True), (1100, True), (1900, True), (2000, False), (2100, False))]
    assert events.count('long') == 1 and 'short' not in events
