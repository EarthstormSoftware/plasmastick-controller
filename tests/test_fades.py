import asyncio
import json
import time

import pytest

import mqttstate
from deviceconfig import DeviceConfig
from ledstrip import MODES, LedStrip


@pytest.fixture
def strip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'device': {'device_led_count': 4, 'transition_ms': 400}}))
    s = LedStrip(DeviceConfig.loadConfigFromFile())
    s.set_brightness(100)
    return s


def run(strip, steps):
    """Run the controller, doing each (seconds to wait, action) in turn; returns pixel 0 after each wait."""
    async def go():
        task = asyncio.create_task(strip.controller())
        seen = []
        for wait, action in steps:
            if action:
                action()
            await asyncio.sleep(wait)
            seen.append(strip.led_strip.pixels[0])
        task.cancel()
        return seen
    return asyncio.run(go())


def red(p):
    return 0 if p is None else p[0]


def test_switching_on_fades_in(strip):
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    seen = run(strip, [(0.1, lambda: strip.set_next_state(True)), (0.15, None), (0.4, None)])
    assert 0 < red(seen[0]) < red(seen[1]) < 255 and seen[2] == (255, 0, 0)
    assert strip.report()['state'] is True


def test_switching_off_fades_out_then_clears(strip):
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    strip.set_next_state(True)
    strip.device_config.transition_ms = 0
    seen = run(strip, [(0.1, None), (0.15, lambda: (setattr(strip.device_config, 'transition_ms', 400),
                                                     strip.set_next_state(False))), (0.5, None)])
    assert seen[0] == (255, 0, 0)
    assert 0 < red(seen[1]) < 255  # on its way down
    assert seen[2] is None  # cleared once dark
    assert strip.report()['state'] is False  # reported off straight away


def test_brightness_fades(strip):
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    strip.set_next_state(True)
    seen = run(strip, [(0.6, None), (0.15, lambda: strip.set_brightness(10)), (0.5, None)])
    assert seen[0] == (255, 0, 0)
    assert red(seen[2]) < red(seen[1]) < 255


def test_a_new_colour_cross_fades(strip):
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    strip.set_next_state(True)
    seen = run(strip, [(0.6, None), (0.15, lambda: strip.set_next_mode('Colour', {'Colour': '#0000ff'})), (0.5, None)])
    mid = seen[1]
    assert seen[0] == (255, 0, 0) and 0 < mid[0] < 255 and 0 < mid[2] < 255 and seen[2] == (0, 0, 255)


def test_no_fade_when_set_to_zero(strip):
    strip.device_config.transition_ms = 0
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    seen = run(strip, [(0.15, lambda: strip.set_next_state(True)), (0.15, lambda: strip.set_next_state(False))])
    assert seen == [(255, 0, 0), None]


def test_a_change_can_have_its_own_fade_time(strip):
    strip.set_next_mode('Colour', {'Colour': '#ff0000'})
    seen = run(strip, [(0.15, lambda: strip.set_next_state(True, transition_ms=0))])
    assert seen == [(255, 0, 0)]  # the request's 0 beats the setting's 400 ms


def test_animations_fade_in_too(strip):
    strip.set_next_mode('Rainbows')
    seen = run(strip, [(0.1, lambda: strip.set_next_state(True)), (0.5, None)])
    assert max(seen[0]) < max(seen[1])


def test_home_assistant_transition_comes_first():
    assert mqttstate.parse_ha_command({'state': 'ON', 'transition': 2.5}, MODES) == [
        ('transition', 2500), ('power', True)]
    with pytest.raises(ValueError):
        mqttstate.parse_ha_command({'state': 'ON', 'transition': -1}, MODES)
