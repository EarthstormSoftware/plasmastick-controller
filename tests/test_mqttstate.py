import asyncio
import json

import pytest

import mqttgroup
import mqttha
import mqttstate
from deviceconfig import DeviceConfig
from ledstrip import MODES, LedStrip, light_output


def test_topics():
    t = mqttstate.topics('PorchLights')
    assert t['control'] == '/home/device/PorchLights/control'
    assert t['state'] == '/home/device/PorchLights/state'
    assert t['status'] == '/home/device/PorchLights/status'
    assert t['ha_set'] == '/home/device/PorchLights/ha/set'
    assert t['ha_config'] == 'homeassistant/light/PorchLights/config'
    assert t['info'] == '/home/device/PorchLights/info'
    assert t['ha_sensor_rssi'] == 'homeassistant/sensor/PorchLights/rssi/config'


def test_default_config_keeps_the_existing_topics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = DeviceConfig.loadConfigFromFile()
    assert cfg.mqttconfig.topics() == mqttstate.topics(cfg.mqttconfig.client_id)
    assert cfg.mqttconfig.topics()['control'] == '/home/device/' + cfg.mqttconfig.client_id + '/control'


def test_custom_topics():
    t = mqttstate.topics('PorchLights', 'lights/{name}', 'ha')
    assert t['control'] == 'lights/PorchLights/control'
    assert t['ha_set'] == 'lights/PorchLights/ha/set'
    assert t['ha_config'] == 'ha/light/PorchLights/config'
    # A base without {name} suits a single device.
    assert mqttstate.topics('PorchLights', 'porch')['state'] == 'porch/state'


@pytest.mark.parametrize('base', ['/home/device/{name}', 'plasmastick/{name}', 'porch', 'a/{name}/b', 'my lights/{name}'])
def test_good_topic_bases(base):
    assert mqttstate.topic_base_error(base) is None


@pytest.mark.parametrize('base, error', [
    ('', 'topic_base cannot be empty'),
    ('x' * 101, 'topic_base must be at most 100 characters'),
    ('home/+/{name}', 'topic_base cannot contain the wildcards + or #'),
    ('home/#', 'topic_base cannot contain the wildcards + or #'),
    ('home\0', 'topic_base cannot contain a null character'),
    (' home', 'topic_base cannot start or end with a space'),
    ('home/{name}/', 'topic_base cannot end with /'),
    ('home//{name}', 'topic_base cannot contain an empty level (//)'),
    ('home/{id}', 'topic_base only understands the placeholder {name}'),
])
def test_bad_topic_bases(base, error):
    assert mqttstate.topic_base_error(base) == error


def test_ha_prefix_rules():
    assert mqttstate.ha_prefix_error('homeassistant') is None
    assert mqttstate.ha_prefix_error('ha/{name}') == 'ha_prefix cannot contain { or }'
    assert mqttstate.ha_prefix_error('') == 'ha_prefix cannot be empty'


def test_hand_edited_unusable_topics_fall_back_to_the_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'mqtt': {'topic_base': 'bad/#', 'ha_prefix': ''}}))
    cfg = DeviceConfig.loadConfigFromFile()
    assert cfg.mqttconfig.topics() == mqttstate.topics(cfg.mqttconfig.client_id)


def test_retired_topics_are_the_retained_ones_no_longer_used():
    old = mqttstate.topics('Porch')
    assert mqttstate.retired_topics(old, old) == []
    moved = mqttstate.retired_topics(old, mqttstate.topics('Porch', 'lights/{name}'))
    # The discovery configs stay where they were, so they aren't cleared.
    assert moved == ['/home/device/Porch/state', '/home/device/Porch/status', '/home/device/Porch/info',
                     '/home/device/Porch/ha/state']
    renamed = mqttstate.retired_topics(old, mqttstate.topics('Garden'))
    assert 'homeassistant/light/Porch/config' in renamed and '/home/device/Porch/control' not in renamed


def test_retired_topics_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert DeviceConfig.retired_topics() == []
    DeviceConfig.retire_topics(['a/state', 'a/status'])
    DeviceConfig.retire_topics(['a/status', 'b/state'])
    assert DeviceConfig.retired_topics() == ['a/state', 'a/status', 'b/state']
    DeviceConfig.clear_retired_topics()
    assert DeviceConfig.retired_topics() == []
    DeviceConfig.clear_retired_topics()  # nothing to clear is fine


def test_state_payloads():
    on = {'state': True, 'mode': 'Colour', 'colour': '#ffc0cb', 'brightness': 40}
    assert json.loads(mqttstate.state_payload(on)) == {
        'State': True, 'Mode': 'Colour', 'ModeParameters': {'Colour': '#ffc0cb'}, 'Brightness': 40}
    assert json.loads(mqttstate.ha_state_payload(on)) == {
        'state': 'ON', 'color_mode': 'rgb', 'effect': 'Colour', 'brightness': 40,
        'color': {'r': 255, 'g': 192, 'b': 203}}
    rainbows = {'state': False, 'mode': 'Rainbows', 'colour': None, 'brightness': 100}
    assert json.loads(mqttstate.state_payload(rainbows))['ModeParameters'] == {}
    assert 'color' not in json.loads(mqttstate.ha_state_payload(rainbows))


def test_discovery_payload():
    config = json.loads(mqttha.ha_discovery_payload('PorchLights', mqttstate.topics('PorchLights'), MODES))
    assert config['schema'] == 'json'
    assert config['unique_id'] == 'PorchLights_light'
    assert config['command_topic'] == '/home/device/PorchLights/ha/set'
    assert config['state_topic'] == '/home/device/PorchLights/ha/state'
    assert config['availability_topic'] == '/home/device/PorchLights/status'
    assert config['effect_list'] == list(MODES)
    assert config['brightness'] is True and config['brightness_scale'] == 100
    assert config['device']['identifiers'] == ['PorchLights']


@pytest.mark.parametrize('payload, actions', [
    ({'state': 'OFF'}, [('power', False)]),
    ({'state': 'ON', 'color': {'r': 255, 'g': 0, 'b': 16}}, [('mode', 'Colour', {'Colour': '#ff0010'}), ('power', True)]),
    ({'state': 'ON', 'effect': 'Rainbows'}, [('mode', 'Rainbows', None), ('power', True)]),
    ({'effect': 'CheerLights'}, [('mode', 'CheerLights', None)]),
    ({'state': 'ON', 'brightness': 40}, [('brightness', 40), ('power', True)]),
    ({'brightness': 0}, [('brightness', 1)]),
    ({'brightness': 49.6}, [('brightness', 50)]),
])
def test_ha_commands(payload, actions):
    assert mqttstate.parse_ha_command(payload, MODES) == actions


@pytest.mark.parametrize('payload', [
    'ON', {}, {'state': 'on'}, {'effect': 'Disco'}, {'color': {'r': 1, 'g': 2}}, {'color': {'r': 256, 'g': 0, 'b': 0}},
    {'brightness': 101}, {'brightness': -1}, {'brightness': '50'}, {'brightness': True},
])
def test_unusable_ha_commands_are_rejected(payload):
    with pytest.raises(ValueError):
        mqttstate.parse_ha_command(payload, MODES)


def test_mqtt_will_marks_the_device_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import sys
    import types
    monkeypatch.setitem(sys.modules, 'mqtt_as', types.SimpleNamespace(config={}))
    cfg = DeviceConfig.loadConfigFromFile()
    assert cfg.get_mqtt_as_config()['will'] == (mqttstate.topics(cfg.mqttconfig.client_id)['status'], 'offline', True, 0)


def test_strip_reports_what_it_shows_and_signals_changes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    strip = LedStrip(DeviceConfig.loadConfigFromFile())

    async def apply(update):
        update()
        strip.changed.clear()
        task = asyncio.create_task(strip.controller())
        await asyncio.wait_for(strip.changed.wait(), 1)
        task.cancel()
        return strip.report()

    async def go():
        on_pink = await apply(lambda: (strip.set_next_state(True), strip.set_next_mode('Colour', {'Colour': '#ffc0cb'})))
        rainbows = await apply(lambda: strip.set_next_mode('Rainbows'))
        christmas = await apply(lambda: strip.set_next_mode('Christmas'))
        fade = await apply(lambda: strip.set_next_mode('Christmas', {'Style': 'Fade'}))
        return on_pink, rainbows, christmas, fade

    on_pink, rainbows, christmas, fade = asyncio.run(go())
    assert on_pink == {'state': True, 'mode': 'Colour', 'colour': '#ffc0cb', 'style': None, 'speed': None,
                       'kelvin': None, 'brightness': 66}
    assert rainbows == {'state': True, 'mode': 'Rainbows', 'colour': None, 'style': None, 'speed': 5, 'kelvin': None,
                        'brightness': 66}
    # No style or speed asked for: the defaults.
    assert christmas == {'state': True, 'mode': 'Christmas', 'colour': None, 'style': 'Twinkle', 'speed': 5,
                         'kelvin': None, 'brightness': 66}
    assert fade['style'] == 'Fade'
    assert json.loads(mqttstate.state_payload(fade))['ModeParameters'] == {'Style': 'Fade', 'Speed': 5}


def test_brightness_scales_what_is_painted_and_signals_a_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    strip = LedStrip(DeviceConfig.loadConfigFromFile())
    strip.device_config.transition_ms = 0  # no fades: see test_fades.py

    async def go():
        strip.set_brightness(100)
        strip.set_next_state(True)
        strip.set_next_mode('Colour', {'Colour': '#ff8000'})
        task = asyncio.create_task(strip.controller())
        await asyncio.sleep(0.15)
        full = strip.led_strip.pixels[0]
        strip.changed.clear()
        strip.set_brightness(50)
        assert strip.changed.is_set()
        await asyncio.sleep(0.15)
        task.cancel()
        return full, strip.led_strip.pixels[0]

    full, half = asyncio.run(go())
    assert full == (255, 128, 0)
    assert half == (137, 69, 0)  # the driver's gamma makes this look about half as bright
    assert strip.report()['brightness'] == 50


def test_brightness_spans_the_visible_range():
    inputs = [round(255 * light_output(p)) for p in range(1, 101)]
    assert inputs == sorted(inputs) and len(set(inputs)) == 100  # every step is a change
    assert inputs[0] == 22  # the dimmest value the driver doesn't turn to 0
    assert inputs[-1] == 255


@pytest.mark.parametrize('payload, shown', [
    ({'created_at': 'x', 'entry_id': 1, 'field2': '#fdf5e6'}, '#fdf5e6'),
    ({'field2': 'ff0000'}, '#ff0000'),         # without the '#'
    ({'channel': {}, 'feeds': [{'field2': '#00ff00'}]}, '#123456'),  # a history feed: not used
    ({'field2': 'purple'}, '#123456'),
    ({}, '#123456'),
    (['#ff0000'], '#123456'),
])
def test_cheerlights_only_uses_a_valid_colour(tmp_path, monkeypatch, payload, shown):
    monkeypatch.chdir(tmp_path)
    import asynchttp
    strip = LedStrip(DeviceConfig.loadConfigFromFile())
    strip.current_state, strip.current_mode, strip.current_colour = True, 'CheerLights', '#123456'

    async def fake_get_json(url):
        return payload

    monkeypatch.setattr(asynchttp, 'get_json', fake_get_json)
    asyncio.run(strip._fetch_cheerlights())
    assert strip.current_colour == shown


def test_info_payload_has_the_health_figures():
    info = {'version': '1.1.0', 'uptime_s': 125, 'mem_free': 86000, 'state': True,
            'wifi': {'ssid': 'Home', 'ip': '192.168.1.20', 'rssi': -61}}
    assert json.loads(mqttstate.info_payload(info)) == {
        'version': '1.1.0', 'uptime_s': 125, 'rssi': -61, 'ip': '192.168.1.20', 'mem_free': 86000}


@pytest.mark.parametrize('key', list(mqttstate.SENSOR_KEYS))
def test_sensor_discovery_reads_the_info_topic(key):
    t = mqttstate.topics('PorchLights')
    config = json.loads(mqttha.ha_sensor_discovery_payload('PorchLights', t, key))
    assert config['state_topic'] == t['info'] and config['availability_topic'] == t['status']
    assert config['value_template'] == '{{ value_json.' + key + ' }}'
    assert config['unique_id'] == 'PorchLights_' + key
    assert config['entity_category'] == 'diagnostic'
    # The same device as the light, so Home Assistant shows them together.
    light = json.loads(mqttha.ha_discovery_payload('PorchLights', t, MODES))
    assert config['device']['identifiers'] == light['device']['identifiers']
    # Every sensor reads a figure the info payload has.
    info = {'version': 'x', 'uptime_s': 1, 'mem_free': 1, 'wifi': {'ip': 'x', 'rssi': -1}}
    assert key in json.loads(mqttstate.info_payload(info))


def test_unknown_sensor():
    with pytest.raises(ValueError):
        mqttha.ha_sensor_discovery_payload('PorchLights', mqttstate.topics('PorchLights'), 'nope')


def test_group_topic_only_when_in_a_group():
    assert 'group' not in mqttstate.topics('Porch')
    t = mqttstate.topics('Porch', group='garden')
    assert t['group'] == 'plasmastick/group/garden'
    assert t['group'] not in [t[k] for k in mqttstate.RETAINED]  # others still use it


@pytest.mark.parametrize('group, ok', [('', True), ('garden', True), ('Back_Yard-2', True), ('x' * 32, True),
                                       ('x' * 33, False), ('a/b', False), ('a+b', False), ('a b', False), (None, False)])
def test_group_names(group, ok):
    assert (mqttstate.group_error(group) is None) == ok


def report(**kw):
    r = {'state': True, 'mode': 'Colour', 'colour': '#ffb46b', 'style': None, 'speed': None, 'kelvin': None,
         'brightness': 66}
    r.update(kw)
    return r


def roundtrip(rep):
    from ledstrip import mode_parameters_error
    return mqttgroup.parse_group(json.loads(mqttgroup.group_payload(rep, 'Porch')), MODES, mode_parameters_error)


def test_group_payload_carries_only_the_defining_parameter():
    assert json.loads(mqttgroup.group_payload(report(), 'Porch')) == {
        'State': True, 'Mode': 'Colour', 'ModeParameters': {'Colour': '#ffb46b'}, 'Brightness': 66, 'From': 'Porch'}
    cheer = json.loads(mqttgroup.group_payload(report(mode='CheerLights', colour='#ff0000'), 'Porch'))
    assert cheer['ModeParameters'] == {}  # each member fetches its own colour
    xmas = json.loads(mqttgroup.group_payload(report(mode='Christmas', colour=None, style='Fade'), 'Porch'))
    assert xmas['ModeParameters'] == {'Style': 'Fade'}


@pytest.mark.parametrize('rep', [report(), report(state=False), report(mode='Rainbows', colour=None),
                                 report(mode='CheerLights', colour='#00ff00'),
                                 report(mode='Christmas', colour=None, style='Twinkle')])
def test_a_devices_own_group_message_changes_nothing(rep):
    assert mqttgroup.group_actions(roundtrip(rep), rep, True) == []


def test_following_the_group():
    group = roundtrip(report(state=True, mode='Christmas', colour=None, style='Fade', brightness=30))
    here = report(state=False, mode='Christmas', colour=None, style='Twinkle', brightness=80)
    assert mqttgroup.group_actions(group, here, True) == [
        ('power', True), ('mode', 'Christmas', {'Style': 'Fade'}), ('brightness', 30)]
    assert mqttgroup.group_actions(group, here, False) == [('power', True), ('mode', 'Christmas', {'Style': 'Fade'})]
    # A colour change is a change; the same colour in other letters isn't.
    assert mqttgroup.group_actions(roundtrip(report(colour='#FFC0CB')), report(colour='#ffc0cb'), True) == []
    assert mqttgroup.group_actions(roundtrip(report(colour='#0000ff')), report(), True) == [
        ('mode', 'Colour', {'Colour': '#0000ff'})]


@pytest.mark.parametrize('payload', [
    'not an object', {'State': 'yes', 'Mode': 'Colour', 'Brightness': 50},
    {'State': True, 'Mode': 'Disco', 'Brightness': 50},
    {'State': True, 'Mode': 'Colour', 'ModeParameters': {'Colour': 'pink'}, 'Brightness': 50},
    {'State': True, 'Mode': 'Christmas', 'ModeParameters': {'Style': 'Disco'}, 'Brightness': 50},
    {'State': True, 'Mode': 'Colour', 'Brightness': 0}, {'State': True, 'Mode': 'Colour', 'Brightness': True}])
def test_unusable_group_messages_are_refused_whole(payload):
    from ledstrip import mode_parameters_error
    with pytest.raises(ValueError):
        mqttgroup.parse_group(payload, MODES, mode_parameters_error)


def test_groups_carry_the_speed():
    rep = report(mode='Christmas', colour=None, style='Fade', speed=8)
    assert json.loads(mqttgroup.group_payload(rep, 'P'))['ModeParameters'] == {'Style': 'Fade', 'Speed': 8}
    assert mqttgroup.group_actions(roundtrip(rep), rep, True) == []
    assert mqttgroup.group_actions(roundtrip(rep), dict(rep, speed=5), True) == [
        ('mode', 'Christmas', {'Style': 'Fade', 'Speed': 8})]


def test_colour_temperature_from_home_assistant():
    assert mqttstate.parse_ha_command({'state': 'ON', 'color_temp': 370}, MODES) == [
        ('mode', 'Colour', {'Kelvin': 2703}), ('power', True)]
    assert mqttstate.parse_ha_command({'color_temp': 600}, MODES) == [('mode', 'Colour', {'Kelvin': 2000})]  # clamped
    with pytest.raises(ValueError):
        mqttstate.parse_ha_command({'color_temp': 'warm'}, MODES)
    config = json.loads(mqttha.ha_discovery_payload('P', mqttstate.topics('P'), MODES))
    assert config['supported_color_modes'] == ['rgb', 'color_temp']
    assert (config['min_mireds'], config['max_mireds']) == (153, 500)


def test_colour_temperature_is_reported_as_such():
    warm = report(colour='#ffa757', kelvin=2700)
    ha = json.loads(mqttstate.ha_state_payload(warm))
    assert ha['color_mode'] == 'color_temp' and ha['color_temp'] == 370
    assert json.loads(mqttstate.ha_state_payload(report()))['color_mode'] == 'rgb'
    assert json.loads(mqttstate.state_payload(warm))['ModeParameters'] == {'Colour': '#ffa757', 'Kelvin': 2700}


def test_a_colour_temperature_becomes_its_white(tmp_path, monkeypatch):
    from ledstrip import kelvin_colour, mode_parameters_error
    assert kelvin_colour(3000) == '#ffb16e' and kelvin_colour(6500) == '#fffefa'
    assert mode_parameters_error('Colour', {'Kelvin': 1500}) == 'Kelvin must be a whole number, 2000-6500'
    monkeypatch.chdir(tmp_path)
    strip = LedStrip(DeviceConfig.loadConfigFromFile())
    strip.device_config.transition_ms = 0

    async def go():
        strip.set_next_state(True)
        strip.set_next_mode('Colour', {'Kelvin': 2700})
        task = asyncio.create_task(strip.controller())
        await asyncio.sleep(0.15)
        warm = strip.report()
        strip.set_next_mode('Colour', {'Colour': '#ff0000'})
        await asyncio.sleep(0.15)
        task.cancel()
        return warm, strip.report()

    warm, red = asyncio.run(go())
    assert warm['colour'] == '#ffa757' and warm['kelvin'] == 2700
    assert red['kelvin'] is None  # a plain colour isn't a temperature


def test_the_sensor_descriptions_match_the_sensor_topics():
    assert tuple(s[0] for s in mqttha.SENSORS) == mqttstate.SENSOR_KEYS


def test_groups_carry_the_colour_temperature():
    warm = report(colour='#ffa757', kelvin=2700)
    plain = report(colour='#ffa757')  # the same colour, but not known as a temperature
    assert mqttgroup.group_actions(roundtrip(warm), warm, True) == []
    assert mqttgroup.group_actions(roundtrip(warm), plain, True) == [
        ('mode', 'Colour', {'Colour': '#ffa757', 'Kelvin': 2700})]
    # Kelvin only means something in Colour mode.
    rainbow = report(mode='Rainbows', colour=None, speed=5, kelvin=None)
    assert 'Kelvin' not in json.loads(mqttgroup.group_payload(rainbow, 'P'))['ModeParameters']
