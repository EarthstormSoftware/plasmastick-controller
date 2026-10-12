import asyncio
import random
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


def run(effect, n, frames, output=1.0):
    strip = Strip(n)
    for _ in range(frames):
        effect.frame(strip, output)
    return strip.pixels


def test_flame_colours_are_warm():
    for h in range(256):
        r, g, b = effects.heat_rgb(h)
        assert r >= g >= b and b <= 96, (h, (r, g, b))


@pytest.mark.parametrize('layout', ['bottle', 'horizontal', 'vertical'])
def test_fire_only_shows_warm_colours(layout):
    random.seed(1)
    fire = effects.Fire(49, layout, False)
    for _ in range(60):
        for r, g, b in run(fire, 49, 1):
            assert r >= g >= b


@pytest.mark.parametrize('layout', ['bottle', 'horizontal'])
def test_embers_never_go_dark(layout):
    random.seed(2)
    fire = effects.Fire(49, layout, False)
    for _ in range(200):
        assert all(r > 0 for r, _, _ in run(fire, 49, 1, light_output(1)))


@pytest.mark.parametrize('reverse', [False, True])
def test_vertical_flames_are_hottest_at_the_bottom(reverse):
    random.seed(3)
    n = 40
    fire = effects.Fire(n, 'vertical', reverse)
    totals = [0] * n
    for _ in range(300):
        for i, (r, g, b) in enumerate(run(fire, n, 1)):
            totals[i] += r + g
    bottom, top = sum(totals[:10]), sum(totals[-10:])
    assert (top > 2 * bottom) if reverse else (bottom > 2 * top)


def test_horizontal_flames_move_along_the_strip():
    random.seed(4)
    fire = effects.Fire(49, 'horizontal', False)
    first = list(run(fire, 49, 1))
    assert run(fire, 49, 30) != first


def test_water_colours_are_blue_green():
    for v in range(256):
        r, g, b = effects.ocean_rgb(v)
        assert r <= 170 and max(g, b) >= r and (g + b) > 0, (v, (r, g, b))
    assert effects.ocean_rgb(0) == (0, 8, 40) and effects.ocean_rgb(255) == (170, 255, 255)


@pytest.mark.parametrize('layout', ['bottle', 'horizontal', 'vertical'])
def test_ocean_only_shows_water_colours(layout):
    random.seed(5)
    ocean = effects.Ocean(49, layout, False)
    for _ in range(100):
        for r, g, b in run(ocean, 49, 1):
            assert max(g, b) >= r and r <= 170


@pytest.mark.parametrize('reverse', [False, True])
def test_vertical_ocean_is_deepest_at_the_bottom(reverse):
    random.seed(6)
    n = 30
    ocean = effects.Ocean(n, 'vertical', reverse)
    totals = [0] * n
    for _ in range(200):
        for i, p in enumerate(run(ocean, n, 1)):
            totals[i] += sum(p)
    bottom, top = sum(totals[:8]), sum(totals[-8:])
    assert (bottom > top) if reverse else (top > bottom)


def test_ocean_glints_now_and_then():
    random.seed(7)
    ocean = effects.Ocean(49, 'bottle', False)
    pale = sum(1 for _ in range(300) for r, g, b in run(ocean, 49, 1) if r > 100)
    assert 0 < pale < 300 * 49 // 20


def test_reversed_horizontal_waves_travel_the_other_way():
    a, b = effects.Ocean(40, 'horizontal', False), effects.Ocean(40, 'horizontal', True)
    random.seed(8)
    pa = run(a, 40, 20)
    random.seed(8)
    pb = run(b, 40, 20)
    assert pa != pb
