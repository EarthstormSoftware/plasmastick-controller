import asyncio
import random
import json

import pytest

import effects
import product
from base.light import Light, LIGHT_SETTINGS
from base.schedule import schedule_settings
from base.settings import Settings, merge

EFFECT_MODES = ('Rainbows', 'Fire', 'Ocean', 'Christmas')
MODES = ('Colour',) + product.MODE_ORDER


def light_output(percent):
    """The share of full output percent brightness draws, as the light works it out."""
    return product.MIN_LEVEL + (1 - product.MIN_LEVEL) * (percent - 1) / 99


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


class Output:
    """The strip as the light sees it: effects draw into .strip."""
    def __init__(self, n):
        self.count = n
        self.strip = Strip(n)

    def paint(self, colour, level):
        for i in range(self.count):
            self.strip.set_rgb(i, *(int(c * level + 0.5) for c in colour))

    def clear(self):
        self.strip.pixels = [(0, 0, 0)] * self.count


def settings(tmp_path, **device):
    s = Settings(str(tmp_path / 'config.json'), merge(LIGHT_SETTINGS, schedule_settings(MODES), product.SETTINGS))
    if device:
        assert s.update({'device': device})[0] is None
    return s


@pytest.fixture
def light(tmp_path):
    return Light(Output(12), settings(tmp_path, default_state=True, transition_ms=0), product.LIGHT_MODES, product,
                 product.MIN_LEVEL)


def test_the_modes_are_1_4s_in_its_order(light):
    assert light.modes == ('Colour', 'CheerLights', 'Rainbows', 'Fire', 'Ocean', 'Christmas')


def test_layout_defaults_to_bottle(light):
    assert (light.settings.get('device', 'led_layout'), light.settings.get('device', 'led_reverse')) == ('bottle', False)


def test_a_layout_change_remakes_the_animation(light):
    light.set(mode='Rainbows')
    first = light.effect()
    assert light.effect() is first
    light.settings.update({'device': {'led_layout': 'horizontal'}})
    product.applied(type('D', (), {'light': light}), {('device', 'led_layout')})
    second = light.effect()
    assert second is not first
    light.settings.update({'device': {'led_reverse': True}})
    product.applied(type('D', (), {'light': light}), {('device', 'led_reverse')})
    third = light.effect()
    assert third is not second and list(third.pos) == list(second.pos)[::-1]


def test_a_bottle_ignores_reverse(light):
    light.settings.update({'device': {'led_reverse': True}})
    light.set(mode='Rainbows')
    assert list(light.effect().pos) == sorted(light.effect().pos)


def test_a_christmas_style_remakes_it_and_shows_in_the_state(light):
    light.set(mode='Christmas')
    first = light.effect()
    assert light.state()['ModeParameters'] == {'Speed': 5, 'Style': 'Twinkle'}
    assert light.set(parameters={'Style': 'Fade'}) is None
    assert light.effect() is not first
    assert light.state()['ModeParameters']['Style'] == 'Fade'
    assert light.set(parameters={'Style': 'Disco'}) is not None


def test_the_light_animates_the_strip(light):
    async def run():
        light.set(mode='Rainbows', brightness=100, transition_ms=0)
        task = asyncio.create_task(light.run())
        await asyncio.sleep(0.15)
        first = list(light.output.strip.pixels)
        await asyncio.sleep(0.15)
        task.cancel()
        return first, list(light.output.strip.pixels)
    first, later = asyncio.run(run())
    assert None not in first and later != first


def test_unusable_saved_layout_falls_back_to_bottle(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'device': {'led_layout': 'spiral'}}))
    assert settings(tmp_path).get('device', 'led_layout') == 'bottle'


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
        assert r <= 60 and max(g, b) >= r and (g + b) > 0, (v, (r, g, b))
    assert effects.ocean_rgb(0) == (0, 8, 40) and effects.ocean_rgb(255) == (60, 210, 200)


@pytest.mark.parametrize('layout', ['bottle', 'horizontal', 'vertical'])
def test_ocean_only_shows_water_colours(layout):
    random.seed(5)
    ocean = effects.Ocean(49, layout, False)
    for _ in range(100):
        for r, g, b in run(ocean, 49, 1):
            assert max(g, b) >= r and r <= 60


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


def test_ocean_has_no_flashes():
    # Its brightest is aqua: no LED ever jumps to a pale flash.
    random.seed(7)
    ocean = effects.Ocean(49, 'bottle', False)
    top = max(max(p) for _ in range(300) for p in run(ocean, 49, 1))
    assert top <= 210


def test_reversed_horizontal_waves_travel_the_other_way():
    a, b = effects.Ocean(40, 'horizontal', False), effects.Ocean(40, 'horizontal', True)
    random.seed(8)
    pa = run(a, 40, 20)
    random.seed(8)
    pb = run(b, 40, 20)
    assert pa != pb


def test_christmas_bulbs_repeat_red_green_gold_blue():
    random.seed(9)
    pixels = run(effects.Christmas(8, 'horizontal', False, 'Twinkle'), 8, 1)
    red, green, gold, blue = pixels[:4]
    assert red[0] > red[1] and green[1] > green[0] and gold[0] > gold[1] > gold[2] and blue[2] > blue[0]
    assert pixels[4:] != [None] * 4


def test_twinkling_bulbs_change_brightness():
    random.seed(10)
    xmas = effects.Christmas(40, 'bottle', False, 'Twinkle')
    seen = {run(xmas, 40, 1, light_output(66))[0] for _ in range(300)}
    assert len(seen) > 5


@pytest.mark.parametrize('percent', [1, 2, 5, 10])
def test_dim_twinkling_bulbs_stay_lit_and_keep_their_colour(percent):
    random.seed(11)
    xmas = effects.Christmas(8, 'bottle', False, 'Twinkle')
    for _ in range(300):
        red, green, gold, blue = run(xmas, 8, 1, light_output(percent))[:4]
        for p in (red, green, gold, blue):
            assert max(p) >= effects.MIN_VISIBLE, (percent, p)
        assert red[0] > red[1] and green[1] > green[0] and blue[2] > blue[0]


def test_fade_holds_then_moves_each_bulb_to_the_next_colour():
    xmas = effects.Christmas(8, 'horizontal', False, 'Fade')
    strip = Strip(8)
    xmas.frame(strip, 1.0)
    start = list(strip.pixels)
    for _ in range(100):  # still holding
        xmas.frame(strip, 1.0)
    assert strip.pixels == start
    for _ in range(156):  # one full colour step on
        xmas.frame(strip, 1.0)
    assert strip.pixels == start[1:] + start[:1]


def test_reversed_fade_drifts_the_other_way():
    a, b = effects.Christmas(8, 'horizontal', False, 'Fade'), effects.Christmas(8, 'horizontal', True, 'Fade')
    sa, sb = Strip(8), Strip(8)
    for _ in range(256):
        a.frame(sa, 1.0)
        b.frame(sb, 1.0)
    assert sa.pixels != sb.pixels


def test_christmas_style_is_checked(light):
    assert light.parameters_error('Christmas', {'Style': 'Fade'}) is None
    assert light.parameters_error('Christmas', {'Style': 'Disco'}) == 'Style must be one of Twinkle, Fade'


def test_changing_the_default_style_remakes_the_animation(light):
    light.set(mode='Christmas')
    assert not light.effect().fade
    light.settings.update({'device': {'christmas_style': 'Fade'}})
    product.applied(type('D', (), {'light': light}), {('device', 'christmas_style')})
    assert light.effect().fade


def test_colour_tables_are_built_once_and_shared():
    a = effects.Rainbows(10, 'bottle', False)
    b = effects.Rainbows(20, 'horizontal', True)
    assert a.table is b.table is effects.colour_table(effects.hue_rgb)
    assert effects.Fire(5, 'bottle', False).table is effects.colour_table(effects.heat_rgb)


@pytest.mark.parametrize('name', sorted(effects.EFFECTS))
def test_a_fade_changes_only_the_brightness_not_the_colours(name):
    random.seed(12)
    effect = effects.EFFECTS[name](12, 'horizontal', False, 'Fade')
    table = getattr(effect, 'table', None)
    strip = Strip(12)
    for step in range(20):
        effect.frame(strip, 0.2 + step * 0.04)
    assert getattr(effect, 'table', None) is table


def test_speed_scales_the_smooth_effects_and_the_flicker_rate():
    slow, usual, fast = (effects.Rainbows(10, 'bottle', False) for _ in range(3))
    slow.speed, fast.speed = 1, 10
    strip = Strip(10)
    for e in (slow, usual, fast):
        e.frame(strip, 1.0)
    assert slow.offset < usual.offset < fast.offset
    assert effects.frame_ms(5) == effects.FRAME_MS and effects.frame_ms(10) < effects.FRAME_MS < effects.frame_ms(1)
    fire = effects.Fire(10, 'bottle', False)
    fire.speed = 10
    assert fire.frame(strip, 1.0) == effects.frame_ms(10)


def test_speed_is_checked_and_changes_without_restarting(light):
    assert light.parameters_error('Rainbows', {'Speed': 7}) is None
    assert light.parameters_error('Rainbows', {'Speed': 11}) == 'Speed must be a whole number, 1-10'
    light.set(mode='Rainbows')
    first = light.effect()
    assert light.speed() == 5
    light.set(parameters={'Speed': 9})
    assert light.effect() is first and light.speed() == 9


def test_reselecting_a_mode_carries_the_animation_on(light):
    async def go():
        light.set(on=True, mode='Rainbows', transition_ms=0)
        task = asyncio.create_task(light.run())
        await asyncio.sleep(0.1)
        first = light._effect
        light.set(mode='Rainbows', parameters={'Speed': 8})
        await asyncio.sleep(0.1)
        same = light._effect
        light.set(mode='Fire')
        await asyncio.sleep(0.1)
        task.cancel()
        return first, same, light._effect
    first, same, fire = asyncio.run(go())
    assert same is first and same.speed == 8  # the light sets it on each frame
    assert isinstance(fire, effects.Fire)
