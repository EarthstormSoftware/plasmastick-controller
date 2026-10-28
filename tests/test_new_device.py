import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import new_device  # noqa: E402

EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'device-template.example.json')


@pytest.mark.parametrize('name, ok', [('Kitchen', True), ('Counter-Top_2', True), ('x' * 32, True),
                                      ('', False), ('x' * 33, False), ('Living Room', False), ('a/b', False),
                                      ('a+b', False)])
def test_device_names(name, ok):
    assert (new_device.name_error(name) is None) == ok


def test_the_example_template_builds_a_config_with_overrides():
    template = new_device.read_template(EXAMPLE)
    config = new_device.build_config(template, 'Kitchen', [
        ('device', 'device_led_count', 75), ('device', 'device_led_rgbw', None), ('mqtt', 'group', 'Downstairs')])
    assert config['device']['device_name'] == 'Kitchen' and config['mqtt']['user'] == 'Kitchen'
    assert config['device']['device_led_count'] == 75  # overridden
    assert config['device']['device_led_rgbw'] is False  # None leaves the template's value
    assert config['mqtt']['group'] == 'Downstairs' and config['mqtt']['enabled'] is True
    assert 'password' not in json.dumps(config)  # passwords are added on the board, encrypted
    assert template['device'] is not config['device']  # the template itself is left alone


def test_the_built_config_passes_the_firmwares_checks(tmp_path):
    import product
    from base import pico
    from base.lightmqtt import LIGHT_MQTT_SETTINGS
    from base.light import LIGHT_SETTINGS
    from base.mqtt import MQTT_SETTINGS
    from base.schedule import schedule_settings
    from base.settings import BASE_SETTINGS, merge
    known = merge(BASE_SETTINGS, MQTT_SETTINGS, LIGHT_SETTINGS, schedule_settings(('Colour',) + product.MODE_ORDER),
                  LIGHT_MQTT_SETTINGS, pico.SETTINGS, product.SETTINGS)
    config = product.Codec.load(new_device.build_config(new_device.read_template(EXAMPLE), 'Kitchen', []))
    for section, values in config.items():
        for key, value in values.items():
            setting = known[section][key]
            assert setting.check is None or setting.check(value) is None, (section, key, value)


@pytest.mark.skipif(sys.platform != 'win32', reason='DPAPI is Windows only')
def test_wifi_details_are_kept_encrypted_for_this_user(tmp_path, monkeypatch):
    monkeypatch.setattr(new_device, 'SECRETS_DIR', str(tmp_path))
    monkeypatch.setattr(new_device, 'WIFI_FILE', str(tmp_path / 'wifi.dpapi'))
    new_device.save_wifi('MyNetwork', 'correct horse battery staple')
    raw = (tmp_path / 'wifi.dpapi').read_bytes()
    assert b'staple' not in raw and b'MyNetwork' not in raw
    assert new_device.load_wifi() == {'ssid': 'MyNetwork', 'password': 'correct horse battery staple'}
