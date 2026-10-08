import base64
import json

import pytest

from api import DeviceAPI
from deviceconfig import DeviceConfig
from httpserver import origin_allowed, password_ok
from ledstrip import LedStrip


def basic(credentials):
    return {'Authorization': 'Basic ' + base64.b64encode(credentials.encode()).decode()}


@pytest.mark.parametrize('headers, allowed', [
    ({'Host': '192.168.1.50'}, True),                                      # curl, scripts
    ({'Host': '192.168.1.50', 'Origin': 'http://192.168.1.50'}, True),   # the device's own page
    ({'Host': 'jack.local', 'Origin': 'http://jack.local/'}, True),
    ({'Host': '192.168.1.50', 'Origin': 'https://evil.example'}, False),
    ({'Host': '192.168.1.50', 'Origin': 'null'}, False),                   # sandboxed or file:// pages
    ({'Host': '192.168.1.50:8080', 'Origin': 'http://192.168.1.50'}, False),
])
def test_origin_check(headers, allowed):
    assert origin_allowed(headers) is allowed


def test_password_check():
    assert password_ok(basic('any:s3cret'), 's3cret')
    assert password_ok(basic(':s3cret'), 's3cret')
    assert password_ok(basic('me:pa:ss'), 'pa:ss')  # colons after the first belong to the password
    assert not password_ok(basic('me:wrong'), 's3cret')
    assert not password_ok({}, 's3cret')
    assert not password_ok({'Authorization': 'Bearer s3cret'}, 's3cret')
    assert not password_ok({'Authorization': 'Basic !!!not-base64'}, 's3cret')


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({}))
    cfg = DeviceConfig.loadConfigFromFile()
    return DeviceAPI(cfg, LedStrip(cfg), pico_led=None)


def test_no_password_by_default(api):
    assert api.web_password == ''
    assert api.get_config()['web'] == {'password_set': False}


def test_setting_and_clearing_the_password(api):
    assert api.save_config({'web': {'password': 'abc'}}) == (False, 'Web password must be at least 4 characters')
    assert api.save_config({'web': {'password': 's3cret'}}) == (True, None)
    assert api.web_password == 's3cret'  # applies straight away
    assert api.get_config()['web'] == {'password_set': True}
    assert 's3cret' not in json.dumps(api.get_config())
    api.save_config({'mqtt': {'user': 'me'}})
    assert api.web_password == 's3cret'  # kept by unrelated saves
    assert api.save_config({'web': {'clear_password': True}}) == (True, None)
    assert api.web_password == ''


def test_setup_mode_clears_a_forgotten_password(api):
    api.save_config({'web': {'password': 's3cret'}})
    DeviceConfig.clear_web_password()
    assert DeviceConfig.loadConfigFromFile().web_password == ''
