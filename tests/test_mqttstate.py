import asyncio
import json

import pytest

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


def test_control_topic_matches_the_existing_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = DeviceConfig.loadConfigFromFile()
    assert mqttstate.topics(cfg.mqttconfig.client_id)['control'] == cfg.get_control_topic()


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
    config = json.loads(mqttstate.ha_discovery_payload('PorchLights', MODES))
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
        return on_pink, rainbows

    on_pink, rainbows = asyncio.run(go())
    assert on_pink == {'state': True, 'mode': 'Colour', 'colour': '#ffc0cb', 'brightness': 66}
    assert rainbows == {'state': True, 'mode': 'Rainbows', 'colour': None, 'brightness': 66}


def test_brightness_scales_what_is_painted_and_signals_a_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    strip = LedStrip(DeviceConfig.loadConfigFromFile())

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
