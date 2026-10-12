import json

import pytest

import mqttstate
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
    ({'mqtt': {'topic_base': 'home/#'}}, 'topic_base cannot contain the wildcards + or #'),
    ({'mqtt': {'ha_prefix': ''}}, 'ha_prefix cannot be empty'),
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


def test_log_level_is_saved_and_checked(api):
    assert api.save_config({'device': {'log_level': 3}}) == (False, 'log_level must be 0, 1 or 2')
    assert api.save_config({'device': {'log_level': 0}}) == (True, None)
    assert saved_config(api)['device']['log_level'] == 0
    assert api.get_config()['device']['log_level'] == 0  # applied at once, no restart
    api.save_config({'device': {'log_level': 1}})


def test_info_reports_health(api):
    info = api.get_device_info()
    assert info['version']
    assert info['colour'] is None  # nothing painted yet
    assert info['uptime_s'] >= 0
    assert info['wifi'] == {'ssid': '', 'ip': '192.168.4.1', 'rssi': None}  # not connected
    assert info['mqtt'] == {'enabled': False, 'connected': False, 'error': None}
    assert info['mem_free'] == 120000


def test_info_reports_mqtt_problems(api):
    class Client:
        def isconnected(self):
            return False
    api.mqtt_client = Client()
    api.mqtt_error = "(-1, 'CONNACK reason code 0x5'). Check the MQTT user and password."
    assert api.get_device_info()['mqtt'] == {'enabled': True, 'connected': False, 'error': api.mqtt_error}


def test_live_settings_apply_without_a_restart(api):
    assert api.save_config({'device': {'default_mode': 'Rainbows', 'cheerlights_update_interval': 120,
                                       'mode_parameters': {'Colour': '#00ff00'}}}) == (True, None)
    assert api.device_config.default_mode == 'Rainbows'
    assert api.device_config.cheerlights_update_interval == 120
    assert api.device_config.mode_parameters == {'Colour': '#00ff00'}
    assert not api.restart_required()


def test_log_level_applies_at_once(api):
    from log import Log
    api.save_config({'device': {'log_level': 2}})
    assert Log.level == 2
    api.save_config({'device': {'log_level': 1}})
    assert Log.level == 1


@pytest.mark.parametrize('updates', [
    {'device': {'device_led_count': 50}},
    {'wlan': {'reconnect_wait': 45}},
    {'mqtt': {'broker_address': 'mqtt.local'}},
    {'mqtt': {'topic_base': 'lights/{name}'}},
    {'mqtt': {'ha_prefix': 'ha'}},
])
def test_boot_only_settings_need_a_restart(api, updates):
    api.save_config(updates)
    assert api.restart_required()


def test_saving_unchanged_boot_settings_needs_no_restart(api):
    api.save_config({'device': {'device_led_count': 49}, 'wlan': {'reconnect_wait': api.device_config.wlanconfig.reconnect_wait}})
    assert not api.restart_required()


def test_a_rename_needs_a_restart(api):
    assert api.set_device_name('kitchen')
    assert api.restart_required()


def test_set_brightness(api):
    assert api.set_brightness(30)
    assert api.led_strip.brightness == 30
    assert api.set_brightness(49.6)
    assert api.led_strip.brightness == 50
    assert api.set_brightness(0.6)
    assert api.led_strip.brightness == 1
    api.set_brightness(30)
    for bad in (0, 0.4, 101, 100.6, -1.0, '50', True, None):
        assert not api.set_brightness(bad)
    assert api.led_strip.brightness == 30


def test_default_brightness_is_saved_checked_and_applied_live(api):
    assert api.save_config({'device': {'default_brightness': 0}}) == (False, 'default_brightness must be a whole number, 1-100')
    assert api.save_config({'device': {'default_brightness': 25}}) == (True, None)
    assert saved_config(api)['device']['default_brightness'] == 25
    assert api.get_config()['device']['default_brightness'] == 25
    assert not api.restart_required()


def test_topic_settings_are_shown_and_saved_trimmed(api):
    mq = api.get_config()['mqtt']
    assert (mq['topic_base'], mq['ha_prefix']) == ('/home/device/{name}', 'homeassistant')
    assert api.save_config({'mqtt': {'topic_base': ' lights/{name} ', 'ha_prefix': 'ha'}}) == (True, None)
    assert saved_config(api)['mqtt']['topic_base'] == 'lights/{name}'
    assert api.get_config()['mqtt']['topic_base'] == 'lights/{name}'


def test_changing_topics_retires_the_old_retained_ones(api):
    running = api.device_config.mqttconfig.topics()
    api.save_config({'mqtt': {'user': 'me'}})
    assert DeviceConfig.retired_topics() == []
    api.save_config({'mqtt': {'topic_base': 'lights/{name}'}})
    assert DeviceConfig.retired_topics() == [running['state'], running['status'], running['info'], running['ha_state']]


def test_a_rename_retires_the_old_retained_topics(api):
    running = api.device_config.mqttconfig.topics()
    assert api.set_device_name('kitchen')
    assert running['ha_config'] in DeviceConfig.retired_topics()


@pytest.mark.parametrize('keep_wifi', [True, False])
def test_a_reset_retires_the_retained_topics_in_use(api, keep_wifi):
    running = api.device_config.mqttconfig.topics()
    api.reset(keep_wifi=keep_wifi)
    assert DeviceConfig.retired_topics() == [running[k] for k in mqttstate.RETAINED]


def test_info_lists_the_modes(api):
    from ledstrip import MODES
    assert list(api.get_device_info()['modes']) == list(MODES)


def test_a_config_save_can_rename_the_device(api):
    assert api.save_config({'device': {'device_name': ' Porch Lights ', 'default_state': True}}) == (True, None)
    assert api.device_name == 'Porch-Lights'
    assert api.restart_required()
    # The name is kept in device_name.json, not config.json.
    assert saved_config(api)['device']['device_name'] == ''
    with open('device_name.json') as fp:
        assert json.load(fp) == {'name': 'Porch-Lights'}


def test_saving_the_same_name_is_not_a_rename(api):
    assert api.save_config({'device': {'device_name': api.device_name}}) == (True, None)
    assert not api.restart_required()


@pytest.mark.parametrize('name', ['', '   ', 'x' * 33])
def test_a_bad_name_rejects_the_whole_save(api, name):
    assert api.save_config({'device': {'device_name': name, 'default_state': True}}) == (
        False, 'device_name must be 1-32 characters')
    assert 'default_state' not in saved_config(api)['device']


def test_every_settings_field_on_the_page_is_a_config_setting(api):
    import os
    import re
    page_file = os.path.join(os.path.dirname(__file__), '..', 'src', 'www', 'index.html')
    page = open(page_file, encoding='utf-8').read()
    names = re.findall(r'name="((?:device|wlan|mqtt|web)\.[\w.]+)"', page)
    assert len(names) > 20
    cfg = api.get_config()
    write_only = {'web.password', 'web.clear_password', 'mqtt.password'}
    for name in names:
        if name in write_only:
            continue
        value = cfg
        for key in name.split('.'):
            assert isinstance(value, dict) and key in value, name
            value = value[key]


def test_led_layout_is_saved_checked_and_applied_live(api):
    assert api.save_config({'device': {'led_layout': 'vertical', 'led_reverse': True}}) == (True, None)
    assert (api.device_config.led_layout, api.device_config.led_reverse) == ('vertical', True)
    assert api.get_config()['device']['led_layout'] == 'vertical'
    assert not api.restart_required()
    assert api.save_config({'device': {'led_layout': 'spiral'}}) == (
        False, 'led_layout must be one of bottle, horizontal, vertical')


def test_christmas_style_default_is_saved_checked_and_applied_live(api):
    assert api.get_config()['device']['christmas_style'] == 'Twinkle'
    assert api.save_config({'device': {'christmas_style': 'Fade'}}) == (True, None)
    assert api.device_config.christmas_style == 'Fade' and not api.restart_required()
    assert api.save_config({'device': {'christmas_style': 'Disco'}}) == (
        False, 'christmas_style must be one of Twinkle, Fade')
    assert api.set_mode('Christmas', {'Style': 'Twinkle'})
    assert not api.set_mode('Christmas', {'Style': 'Disco'})


def test_the_page_offers_exactly_the_christmas_styles():
    import os
    import re
    from deviceconfig import CHRISTMAS_STYLES
    page = open(os.path.join(os.path.dirname(__file__), '..', 'src', 'www', 'index.html'), encoding='utf-8').read()
    assert tuple(re.findall(r'data-style="(\w+)"', page)) == CHRISTMAS_STYLES
    select = page.split('id="christmas_style"')[1].split('</select>')[0]
    assert tuple(re.findall(r'<option value="(\w+)"', select)) == CHRISTMAS_STYLES


def test_watchdog_setting_needs_a_restart(api):
    assert api.get_config()['device']['watchdog'] is True
    assert api.save_config({'device': {'watchdog': False}}) == (True, None)
    assert saved_config(api)['device']['watchdog'] is False
    assert api.restart_required()
    assert api.get_device_info()['watchdog_stall'] is None


def test_group_settings_are_checked_and_need_a_restart(api):
    assert api.get_config()['mqtt']['group'] == '' and api.get_config()['mqtt']['group_brightness'] is True
    assert api.save_config({'mqtt': {'group': 'a/b'}}) == (False, 'group must be up to 32 letters, digits, - or _')
    assert api.save_config({'mqtt': {'group': ' garden ', 'group_brightness': False}}) == (True, None)
    assert saved_config(api)['mqtt']['group'] == 'garden'
    assert api.restart_required()


def test_every_local_change_is_signalled_for_the_group(api):
    for action in (api.power_on, api.power_off, api.toggle_power, lambda: api.set_mode('Rainbows'),
                   lambda: api.set_brightness(40)):
        api.local_change.clear()
        action()
        assert api.local_change.is_set()
    api.local_change.clear()
    assert not api.set_mode('Disco') and not api.set_brightness(0)
    assert not api.local_change.is_set()  # refused changes aren't changes


def test_the_time_is_only_reported_once_the_clock_is_set(api):
    assert api.get_device_info()['time'] is None
    api.clock_set = True
    t = api.get_device_info()['time']
    assert len(t) == 20 and t[4] == '-' and t[10] == 'T' and t.endswith('Z')


def test_uptime_survives_the_clock_being_set(api):
    before = api.get_device_info()['uptime_s']
    api._started -= 5 * 365 * 86400  # as if the clock had just jumped five years forward...
    api.shift_clock(5 * 365 * 86400)  # ...and app.py passed the jump on
    assert api.get_device_info()['uptime_s'] - before < 2


@pytest.mark.parametrize('server, ok', [('pool.ntp.org', True), ('192.168.1.1', True), ('', True),
                                        ('bad host', False), ('x' * 65, False), ('a/b', False)])
def test_ntp_server_is_checked(api, server, ok):
    assert api.save_config({'device': {'ntp_server': server}})[0] == ok


def test_fade_time_is_checked_and_applies_live(api):
    assert api.get_config()['device']['transition_ms'] == 500
    assert api.save_config({'device': {'transition_ms': 1500}}) == (True, None)
    assert api.device_config.transition_ms == 1500 and not api.restart_required()
    assert api.save_config({'device': {'transition_ms': 10001}}) == (False, 'transition_ms must be a whole number, 0-10000')
    assert api.save_config({'device': {'transition_ms': 0.5}})[0] is False
