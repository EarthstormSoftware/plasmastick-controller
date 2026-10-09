import base64
import json

import pytest

from api import DeviceAPI
from deviceconfig import DeviceConfig
from httpserver import origin_allowed, password_ok
from ledstrip import LedStrip
from passwords import check_password, hash_password


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
    stored = hash_password('s3cret')
    assert password_ok(basic('any:s3cret'), stored)
    assert password_ok(basic(':s3cret'), stored)
    assert password_ok(basic('me:pa:ss'), hash_password('pa:ss'))  # colons after the first belong to the password
    assert not password_ok(basic('me:wrong'), stored)
    assert not password_ok({}, stored)
    assert not password_ok({'Authorization': 'Bearer s3cret'}, stored)
    assert not password_ok({'Authorization': 'Basic !!!not-base64'}, stored)


def test_password_hashes():
    stored = hash_password('s3cret')
    assert 's3cret' not in stored and stored.startswith('sha256$1000$')
    assert stored != hash_password('s3cret')  # salted
    assert check_password('s3cret', stored)
    assert not check_password('s3cret!', stored)
    assert check_password('x', hash_password('x', iterations=5))  # the count is read from the hash
    for broken in ('', 'sha256$1000$zz$zz', 'md5$1$00$00', None):
        assert not check_password('s3cret', broken)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({}))
    cfg = DeviceConfig.loadConfigFromFile()
    return DeviceAPI(cfg, LedStrip(cfg), pico_led=None)


def test_no_password_by_default(api):
    assert api.web_password_hash == ''
    assert api.get_config()['web'] == {'password_set': False}


def test_setting_and_clearing_the_password(api):
    assert api.save_config({'web': {'password': 'abc'}}) == (False, 'Web password must be at least 4 characters')
    assert api.save_config({'web': {'password': 's3cret'}}) == (True, None)
    assert check_password('s3cret', api.web_password_hash)  # applies straight away
    assert api.get_config()['web'] == {'password_set': True}
    assert 's3cret' not in json.dumps(api.get_config())
    assert 's3cret' not in (tmp_path_config := open('config.json').read())
    assert check_password('s3cret', json.loads(tmp_path_config)['web']['password_hash'])
    api.accepted_auth = 'Basic remembered'
    api.save_config({'mqtt': {'user': 'me'}})
    assert check_password('s3cret', api.web_password_hash)  # kept by unrelated saves
    assert api.accepted_auth == 'Basic remembered'
    assert api.save_config({'web': {'clear_password': True}}) == (True, None)
    assert api.web_password_hash == '' and api.accepted_auth is None


def test_setup_mode_clears_a_forgotten_password(api):
    api.save_config({'web': {'password': 's3cret'}})
    DeviceConfig.clear_web_password()
    assert DeviceConfig.loadConfigFromFile().web_password_hash == ''


def test_mqtt_password_is_stored_encrypted(api):
    assert api.save_config({'mqtt': {'password': 'brokerpw'}}) == (True, None)
    saved = json.loads(open('config.json').read())['mqtt']
    assert 'brokerpw' not in json.dumps(saved) and 'password' not in saved
    assert DeviceConfig.loadConfigFromFile().mqttconfig.password == 'brokerpw'


def test_plain_text_passwords_from_older_versions_are_converted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({
        'device': {'device_led_count': 49},
        'mqtt': {'broker_address': 'mqtt.local', 'user': 'me', 'password': 'brokerpw'},
        'web': {'password': 'letmein'},
    }))
    cfg = DeviceConfig.loadConfigFromFile()
    assert cfg.mqttconfig.password == 'brokerpw'
    assert check_password('letmein', cfg.web_password_hash)
    text = (tmp_path / 'config.json').read_text()
    assert 'brokerpw' not in text and 'letmein' not in text
    saved = json.loads(text)
    assert saved['device'] == {'device_led_count': 49} and saved['mqtt']['user'] == 'me'
    # Loading again changes nothing more.
    DeviceConfig.loadConfigFromFile()
    assert (tmp_path / 'config.json').read_text() == text


def test_guard_remembers_a_good_login_but_never_lets_a_request_without_one_in(api):
    import asyncio
    from httpserver import HTTPServer
    guard = HTTPServer(api).app.before_request_handlers[0]

    def status(headers):
        request = type('Request', (), {'method': 'GET', 'path': '/api/info', 'headers': headers})()
        result = asyncio.run(guard(request))
        return 200 if result is None else result[1]

    assert status({}) == 200  # no password set
    api.save_config({'web': {'password': 's3cret'}})
    assert status({}) == 401  # nothing remembered yet, no header: still refused
    assert status(basic('me:wrong')) == 401
    assert status(basic('me:s3cret')) == 200
    assert api.accepted_auth == basic('me:s3cret')['Authorization']
    assert status(basic('me:s3cret')) == 200  # remembered
    assert status({}) == 401  # a remembered login doesn't let header-less requests in
    assert status(basic('me:wrong')) == 401
    api.save_config({'web': {'password': 'n3wpass'}})
    assert status(basic('me:s3cret')) == 401  # the old login is forgotten with the old password
