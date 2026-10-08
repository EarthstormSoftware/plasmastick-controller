import json

import pytest

from api import DeviceAPI
from deviceconfig import DeviceConfig
from ledstrip import LedStrip


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'device': {'device_led_count': 49}}))
    cfg = DeviceConfig.loadConfigFromFile()
    return DeviceAPI(cfg, LedStrip(cfg), pico_led=None)


def saved_config(api):
    with open('config.json') as fp:
        return json.load(fp)


def test_save_writes_changes_on_top_of_the_merged_config(api):
    assert api.save_config({'device': {'default_state': True}, 'mqtt': {'broker_address': 'mqtt.local'}}) == (True, None)
    saved = saved_config(api)
    assert saved['device']['default_state'] is True
    assert saved['device']['device_led_count'] == 49
    assert saved['device']['default_mode'] == 'Colour'  # filled in from defaults
    assert saved['mqtt']['broker_address'] == 'mqtt.local'


@pytest.mark.parametrize('updates, error', [
    ({'device': {'device_led_count': 0}}, 'device_led_count must be 1-300'),
    ({'device': {'device_led_count': 301}}, 'device_led_count must be 1-300'),
    ({'device': {'default_mode': 'Disco'}}, 'Invalid default_mode'),
    ({'device': {'cheerlights_update_interval': 5}}, 'cheerlights_update_interval must be >= 10'),
    ({'wlan': {'connect_timeout': 1}}, 'connect_timeout must be >= 5'),
    ({'mqtt': {'password': ''}}, 'MQTT password cannot be empty'),
    ({'mqtt': {'queue_len': 0}}, 'queue_len must be >= 1'),
])
def test_invalid_updates_are_rejected(api, updates, error):
    assert api.save_config(updates) == (False, error)


def test_a_rejected_save_leaves_nothing_half_applied(api):
    ok, _ = api.save_config({'device': {'default_state': True, 'device_led_count': 0}})
    assert not ok
    api.save_config({'mqtt': {'user': 'me'}})
    assert saved_config(api)['device']['default_state'] is False


def test_config_view_hides_passwords(api):
    api.save_config({'mqtt': {'password': 'secret'}})
    assert 'secret' not in json.dumps(api.get_config())


def test_set_mode(api):
    assert api.set_mode('Rainbows')
    assert api.led_strip.next_mode == 'Rainbows'
    assert not api.set_mode('Disco')
    assert api.led_strip.next_mode == 'Rainbows'


def test_toggle_uses_the_requested_state(api):
    assert api.toggle_power() is True
    assert api.toggle_power() is False  # before the LED controller has applied anything


def test_device_name_rules(api):
    assert api.set_device_name('Jack O Lantern')
    assert api.device_name == 'Jack-O-Lantern'
    assert not api.set_device_name('')
    assert not api.set_device_name('x' * 33)


@pytest.mark.parametrize('colour', ['#ffc0cb', 'FFC0CB', '#00aAfF'])
def test_valid_colours_are_accepted(api, colour):
    assert api.set_mode('Colour', {'Colour': colour})
    assert api.led_strip.mode_parameters == {'Colour': colour}


@pytest.mark.parametrize('parameters', [{'Colour': '#ffc0c'}, {'Colour': 'pink'}, {'Colour': 123}, '#ffc0cb', ['x']])
def test_bad_colours_and_parameters_are_rejected(api, parameters):
    api.set_mode('Rainbows')
    assert not api.set_mode('Colour', parameters)
    assert api.led_strip.next_mode == 'Rainbows'  # unchanged


def test_other_modes_ignore_colour_parameters(api):
    assert api.set_mode('Rainbows', {'Colour': 'not checked'})


@pytest.mark.parametrize('updates, error', [
    ({'device': {'mode_parameters': {'Colour': 'red'}}}, 'Colour must be a #RRGGBB hex colour'),
    ({'device': {'mode_parameters': 'x'}}, 'Mode parameters must be an object'),
    ({'device': {'mode_parameters': None}}, 'Mode parameters must be an object'),
    ({'device': {'cheerlights_url': 'api.thingspeak.com/x'}}, 'cheerlights_url must be an http:// or https:// URL'),
    ({'device': {'cheerlights_url': 'ftp://x/'}}, 'cheerlights_url must be an http:// or https:// URL'),
])
def test_bad_saved_colours_and_urls_are_rejected(api, updates, error):
    assert api.save_config(updates) == (False, error)


def test_good_saved_colour_and_url(api):
    ok, _ = api.save_config({'device': {'mode_parameters': {'Colour': '#112233'},
                                        'cheerlights_url': ' http://example.com/last.json '}})
    assert ok
    assert saved_config(api)['device']['cheerlights_url'] == 'http://example.com/last.json'
