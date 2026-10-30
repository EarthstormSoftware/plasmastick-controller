"""The Pico platform's rarer work, loaded when it's needed and let go again afterwards (base.pico's _files()), so its
code isn't in a Pico's small heap the rest of the time: the start (prepare()), the files on the board's root
(config.json's layout, provisioning.json, device_name.json, the Wi-Fi trial's), renaming, changing Wi-Fi, setup
mode's saving, resets, and the stored passwords' encryption. See base.pico for the files.
"""
from base import pico
from base.compat import json, write_atomically
from base.log import Log
from base.pico import (path, _load, restart, setup_mode, TAG, CONFIG, PROVISIONING, NAME, SETUP_REQUEST, WIFI_TRIAL,
                       PREVIOUS)


def encrypt(value):
    from base.pico.crypto import encrypt_password
    return '$'.join(encrypt_password(value))


def decrypt(kept):
    from base.pico.crypto import decrypt_password
    try:
        return decrypt_password(*kept.split('$'))
    except Exception as e:  # another board's file, or a damaged one
        Log.warn(TAG, "Couldn't decrypt the MQTT password: {!r}".format(e))
        return ''


def codec_load(stored):
    """config.json in the old firmware's layout, as the base's settings."""
    stored = dict(stored)
    web = dict(stored.get('web') or {})
    if web.get('password'):  # typed into the file by hand: hashed as the old firmware did
        from base.passwords import hash_password
        web['password'] = hash_password(web['password'])
    else:
        web['password'] = web.get('password_hash', '')
    stored['web'] = web
    mqtt = dict(stored.get('mqtt') or {})
    if mqtt.get('password'):  # by hand too
        mqtt['password'] = encrypt(mqtt['password'])
    elif mqtt.get('password_iv') and mqtt.get('password_enc'):
        mqtt['password'] = mqtt['password_iv'] + '$' + mqtt['password_enc']
    stored['mqtt'] = mqtt
    return stored


def codec_dump(values):
    """The base's settings, in the old firmware's layout."""
    stored = dict(values)
    web = dict(values['web'])
    web['password_hash'] = web.pop('password')
    stored['web'] = web
    mqtt = dict(values['mqtt'])
    password = mqtt.pop('password')
    if password:
        mqtt['password_iv'], mqtt['password_enc'] = password.split('$')
    stored['mqtt'] = mqtt
    return stored


def name():
    """Set on the page, else given in setup mode, else in config.json, else made from the board's id."""
    from base.pico import identity
    given = _load(PROVISIONING) or {}
    configured = (_load(CONFIG) or {}).get('device') or {}
    return identity.clean(identity.renamed(path(NAME)) or given.get('device_name') or configured.get('device_name'),
                          pico.product.NAME_PREFIX)


def rename(new):
    """Saves a new name (from the page), used from the next start. Returns why it couldn't, or None."""
    from base.pico import identity
    if not isinstance(new, str) or not 0 < len(new.strip()) <= 32:
        return 'The name must be 1-32 characters.'
    cleaned = identity.clean(new, pico.product.NAME_PREFIX)
    write_atomically(path(NAME), json.dumps({'name': cleaned}))
    Log.info(TAG, 'Renamed {}: from the next start'.format(cleaned))
    return None


def remove(*names):
    import os
    for name in names:
        try:
            os.remove(path(name))
        except OSError:
            pass


def exists(name):
    import os
    try:
        os.stat(path(name))
        return True
    except OSError:
        return False


def factory_reset():
    """Forgets the settings, the name and the Wi-Fi network, and restarts into setup mode."""
    pico.device.reset()
    remove(PROVISIONING, SETUP_REQUEST, WIFI_TRIAL, PREVIOUS)
    Log.warn(TAG, 'Factory reset: settings and Wi-Fi forgotten')
    restart()


def save_setup(name, ssid, password):
    """What setup mode's page saves: the network and name (provisioning.json), the network on trial. It also clears
    the web password (so a forgotten one can be recovered by someone with the board) and the name set on the page.
    Returns the name kept."""
    from base.pico import identity
    from base.pico.crypto import encrypt_password
    name = identity.clean(name, pico.product.NAME_PREFIX)
    iv, encrypted = encrypt_password(password)
    try:
        write_atomically(path(PROVISIONING), json.dumps({'device_name': name, 'ssid': ssid, 'password_iv': iv,
                                                         'password_enc': encrypted}))
        write_atomically(path(WIFI_TRIAL), 'setup')
        remove(PREVIOUS)
        config = _load(CONFIG)
        if config and isinstance(config.get('web'), dict) and config['web'].get('password_hash'):
            config['web']['password_hash'] = ''
            write_atomically(path(CONFIG), json.dumps(config))
        remove(NAME)
    except OSError as e:
        Log.error(TAG, "Couldn't save the settings: {}".format(e))
        return None
    return name


def change_wifi(ssid, password):
    """Saves a new Wi-Fi network (from the page), used from the next start, on trial: if it doesn't connect within
    wlan.TRIAL_S, the network before is put back. Returns why it couldn't, or None."""
    if not isinstance(ssid, str) or not 0 < len(ssid.strip()) <= 32:
        return 'The network name must be 1-32 characters.'
    if not isinstance(password, str) or len(password) > 64 or 0 < len(password) < 8:
        return 'The password must be 8-64 characters (empty for an open network).'
    from base.pico.crypto import encrypt_password
    try:
        with open(path(PROVISIONING)) as f:
            before = f.read()
    except OSError:
        before = '{}'  # none: a config.json written by hand had the network
    saved = _load(PROVISIONING) or {}
    iv, encrypted = encrypt_password(password)
    new = {'ssid': ssid.strip(), 'password_iv': iv, 'password_enc': encrypted}
    if saved.get('device_name'):
        new['device_name'] = saved['device_name']
    if not exists(WIFI_TRIAL):  # on trial already: the one before that is still the one that worked
        write_atomically(path(PREVIOUS), before)
    write_atomically(path(PROVISIONING), json.dumps(new))
    write_atomically(path(WIFI_TRIAL), 'page')
    Log.info(TAG, 'Wi-Fi changed to {}: from the next start, on trial'.format(new['ssid']))
    return None


def trial():
    """'page' or 'setup' while a new network is on trial, else None."""
    try:
        with open(path(WIFI_TRIAL)) as f:
            return f.read().strip() or 'setup'
    except OSError:
        return None


def proven():
    remove(WIFI_TRIAL, PREVIOUS)
    Log.info(TAG, 'The new Wi-Fi network works')


def give_up():
    """The new network didn't connect: the one before goes back (a change on the page), or setup mode again (a
    network from setup mode, so it can be put right)."""
    if trial() == 'page':
        try:
            with open(path(PREVIOUS)) as f:
                before = f.read()
            if json.loads(before):
                write_atomically(path(PROVISIONING), before)
            else:
                remove(PROVISIONING)
        except (OSError, ValueError):
            pass
        remove(WIFI_TRIAL, PREVIOUS)
        Log.warn(TAG, 'Back to the Wi-Fi network before')
        restart()
    setup_mode()


def take_setup_request():
    import os
    try:
        os.remove(path(SETUP_REQUEST))
        return True
    except OSError:
        return False


def wifi():
    """(ssid, password): from setup mode, or from a config.json written by hand; ('', '') for none."""
    saved = provisioned(path(PROVISIONING))
    if saved:
        return saved['ssid'], saved['password']
    wlan_config = (_load(CONFIG) or {}).get('wlan') or {}
    return wlan_config.get('ssid') or '', wlan_config.get('password') or ''


def provisioned(file):
    """What provisioning.json holds ({"ssid", "password", "device_name"}, the password decrypted), or None."""
    try:
        with open(file) as f:
            data = json.loads(f.read())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or 'ssid' not in data:
        return None
    if 'password_iv' in data and 'password_enc' in data:
        from base.pico.crypto import decrypt_password
        try:
            data['password'] = decrypt_password(data['password_iv'], data['password_enc'])
        except Exception as e:  # another board's file, or a damaged one
            Log.warn(TAG, "Couldn't decrypt the Wi-Fi password: {!r}".format(e))
            return None
    elif 'password' not in data:
        return None
    return data


def fallback_for_s():
    wlan_config = (_load(CONFIG) or {}).get('wlan') or {}
    minutes = wlan_config.get('fallback_for_min')
    return (minutes if isinstance(minutes, int) and 5 <= minutes <= 120 else 10) * 60


def prepare():
    """The start, up to running: the watchdog's note, the LED, setup mode if it's due (which ends with a reset), then
    the device and Wi-Fi, kept in base.pico."""
    from base.pico import wdt as dog
    product = pico.product
    Log.info(TAG, '{} {} starting'.format(product.NAME, product.VERSION))
    # A watchdog left running by a soft reset would restart the board mid-start; watchdog() starts a new one
    dog.disable()
    pico._stall = dog.take_stall_report()
    if pico._stall is not None:
        Log.warn(TAG, 'Restarted by the watchdog: the firmware had stalled for {} s'.format(pico._stall))
    from base.pico.led import Led
    pico.led = Led()
    ssid, password = wifi()
    reason = 'no Wi-Fi saved' if not ssid else None
    if take_setup_request() and not reason:
        reason = 'asked for'
    if not reason and hasattr(product, 'setup_held') and product.setup_held():
        reason = 'held at power-on'
    if reason:
        Log.warn(TAG, 'Setup mode: {}'.format(reason))
        from base.pico import setup
        setup.run(product, pico.led, pico.device_name(), save_setup, fallback_for_s(), bool(ssid))  # ends with a reset
    from base.device import Device
    pico.device = Device(product, pico)
    Log.level = pico.device.settings.get('device', 'log_level')
    Log.info(TAG, 'Name: {}'.format(pico.device_name()))
    from base.pico.wlan import Wlan
    pico.wlan = Wlan(ssid, password, pico.device.settings, pico.led, setup_mode, pico.device_name(),
                     trial=trial() is not None, give_up=pico._give_up, proven=pico._proven)
