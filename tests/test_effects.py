import asyncio
import json

import pytest

import effects
from deviceconfig import DeviceConfig
from ledstrip import EFFECT_MODES, MODES, LedStrip, light_output


class Strip:
    def __init__(self, n):
        self.pixels = [None] * n

    def set_rgb(self, i, r, g, b):
        assert all(isinstance(v, int) and 0 <= v <= 255 for v in (r, g, b)), (r, g, b)
        self.pixels[i] = (r, g, b)


def test_every_animated_mode_has_an_effect():
    assert set(effects.EFFECTS) == set(EFFECT_MODES)
    assert set(EFFECT_MODES) <= set(MODES)


@pytest.mark.parametrize('h, rgb', [(0, (255, 0, 0)), (128, (0, 255, 255)), (171, (2, 0, 255)), (255, (255, 0, 5))])
def test_hue_wheel(h, rgb):
    assert effects.hue_rgb(h) == rgb


def test_every_hue_is_fully_saturated():
    for h in range(256):
        rgb = effects.hue_rgb(h)
        assert max(rgb) == 255 and min(rgb) == 0, (h, rgb)


@pytest.mark.parametrize('name', sorted(effects.EFFECTS))
@pytest.mark.parametrize('layout, reverse', [('bottle', False), ('horizontal', False), ('horizontal', True),
                                             ('vertical', False), ('vertical', True)])
@pytest.mark.parametrize('n', [1, 10, 49, 300])
def test_effects_paint_every_led_within_the_brightness(name, layout, reverse, n):
    effect = effects.EFFECTS[name](n, layout, reverse)
    strip = Strip(n)
    for output in (light_output(1), light_output(66), 1.0):
        for _ in range(3):
            wait = effect.frame(strip, output)
            assert isinstance(wait, int) and 10 <= wait <= 1000
            assert None not in strip.pixels
            assert max(max(p) for p in strip.pixels) <= int(output * 255 + 1)


def test_rainbows_moves_and_spreads_one_rainbow_along_the_strip():
    n = 48
    rainbow = effects.Rainbows(n, 'horizontal', False)
    strip = Strip(n)
    rainbow.frame(strip, 1.0)
    first = list(strip.pixels)
    assert len(set(first)) == n  # every LED a different colour
    for _ in range(50):
        rainbow.frame(strip, 1.0)
    assert strip.pixels != first
    # A full turn takes about 6.7 s at 30 frames a second.
    frames = 0x10000 / effects.Rainbows.STEP
    assert 6.0 < frames * effects.FRAME_MS / 1000 < 7.5


def test_reversed_rainbows_mirror_the_strip():
    n = 30
    a, b = Strip(n), Strip(n)
    effects.Rainbows(n, 'horizontal', False).frame(a, 1.0)
    effects.Rainbows(n, 'horizontal', True).frame(b, 1.0)
    assert a.pixels == b.pixels[::-1]


def test_rainbows_follows_brightness():
    rainbow, strip = effects.Rainbows(10, 'bottle', False), Strip(10)
    rainbow.frame(strip, 1.0)
    bright = max(max(p) for p in strip.pixels)
    rainbow.frame(strip, light_output(10))
    assert max(max(p) for p in strip.pixels) < bright


@pytest.fixture
def strip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'device': {'device_led_count': 12}}))
    return LedStrip(DeviceConfig.loadConfigFromFile())


def test_layout_defaults_to_bottle(strip):
    assert (strip.device_config.led_layout, strip.device_config.led_reverse) == ('bottle', False)


def test_a_layout_change_remakes_the_animation(strip):
    strip.current_mode = 'Rainbows'
    first = strip._animation()
    assert strip._animation() is first
    strip.device_config.led_layout = 'horizontal'
    second = strip._animation()
    assert second is not first
    strip.device_config.led_reverse = True
    third = strip._animation()
    assert third is not second and list(third.pos) == list(second.pos)[::-1]


def test_a_bottle_ignores_reverse(strip):
    strip.current_mode = 'Rainbows'
    strip.device_config.led_reverse = True
    assert list(strip._animation().pos) == sorted(strip._animation().pos)


def test_the_controller_animates(strip):
    async def run():
        strip.set_next_state(True)
        strip.set_next_mode('Rainbows')
        task = asyncio.create_task(strip.controller())
        await asyncio.sleep(0.15)
        first = list(strip.led_strip.pixels)
        await asyncio.sleep(0.15)
        task.cancel()
        return first, list(strip.led_strip.pixels)
    first, later = asyncio.run(run())
    assert None not in first and later != first
    assert strip.current_colour is None


def test_unusable_saved_layout_falls_back_to_bottle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'device': {'led_layout': 'spiral'}}))
    assert DeviceConfig.loadConfigFromFile().led_layout == 'bottle'
