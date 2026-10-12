"""Set up a new Plasma Stick from a template: firmware, Wi-Fi, its own MQTT user
and its settings, in one go.

  py scripts\\new_device.py save-wifi --from-port COM5   # once: copy the Wi-Fi details from a working board
  py scripts\\new_device.py save-wifi --prompt           # ...or type them
  py scripts\\new_device.py setup --port COM6 --name Kitchen [--leds 60 --rgbw --group Downstairs ...]
  py scripts\\new_device.py check --name Kitchen --host 192.168.1.50   # after the broker reload

The board must already have Pimoroni's Plasma Stick 2040 W MicroPython (flash it
first) and nothing else on it. The common settings come from device-template.json
(gitignored: it describes your own network; copy device-template.example.json).
Options on the command line override it for one board.

Secrets: the Wi-Fi password is kept under %APPDATA%\\PlasmaStick, encrypted with
Windows DPAPI so only your Windows account can read it. The MQTT password is
made by Mosquitto Manager for each board. Both are written to the board
encrypted with its own key, and never printed or saved anywhere else.
"""
import argparse
import ctypes
import ctypes.wintypes
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(PROJECT_ROOT, 'device-template.json')
SECRETS_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'PlasmaStick')
WIFI_FILE = os.path.join(SECRETS_DIR, 'wifi.dpapi')
FIRMWARE_BUILD = 'plasma_stick_2040_w'
MIN_FIRMWARE = (1, 27)
NAME_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_')
MARK = 'RESULT-JSON:'


# --- DPAPI: encrypted for the current Windows user ---------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(fn, data):
    blob_in, _keep = _blob(data)
    blob_out = _Blob()
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError('DPAPI failed: ' + str(ctypes.GetLastError()))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(data):
    """data encrypted so only this Windows user can decrypt it."""
    return _crypt(ctypes.windll.crypt32.CryptProtectData, data)


def unprotect(data):
    return _crypt(ctypes.windll.crypt32.CryptUnprotectData, data)


def save_wifi(ssid, password):
    os.makedirs(SECRETS_DIR, exist_ok=True)
    with open(WIFI_FILE, 'wb') as f:
        f.write(protect(json.dumps({'ssid': ssid, 'password': password}).encode()))


def load_wifi():
    try:
        with open(WIFI_FILE, 'rb') as f:
            return json.loads(unprotect(f.read()).decode())
    except FileNotFoundError:
        sys.exit('No saved Wi-Fi details: run  py scripts\\new_device.py save-wifi --from-port COMx  first')


# --- Settings ------------------------------------------------------------------------------------

def name_error(name):
    """None if name works as a device name (it's also the MQTT user and hostname), else why not."""
    if not name or len(name) > 32 or set(name) - NAME_CHARS:
        return 'the name must be 1-32 letters, digits, - or _'
    return None


def build_config(template, name, overrides):
    """The board's config.json (without passwords): the template's device and mqtt
    sections, then the overrides, then the name and the board's own MQTT user."""
    config = {'device': dict(template.get('device', {})), 'wlan': {}, 'mqtt': dict(template.get('mqtt', {})),
              'web': {}}
    for section, key, value in overrides:
        if value is not None:
            config[section][key] = value
    config['device']['device_name'] = name
    config['mqtt']['user'] = name
    config['mqtt'].setdefault('enabled', True)
    return config


def read_template(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit('No template: copy device-template.example.json to device-template.json and fill it in')


# --- The board -----------------------------------------------------------------------------------

def run_on(port, script):
    """Run script on the board; returns what it printed after MARK, as JSON. The script file is
    deleted afterwards (it may carry secrets)."""
    fd, path = tempfile.mkstemp(suffix='.py')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(script)
        r = subprocess.run([sys.executable, '-m', 'mpremote', 'connect', port, 'run', path],
                           capture_output=True, text=True)
    finally:
        os.remove(path)
    if r.returncode:
        sys.exit('running on {} failed: {}'.format(port, r.stderr.strip()[-300:]))
    for line in r.stdout.splitlines():
        if line.startswith(MARK):
            return json.loads(line[len(MARK):])
    sys.exit('no result from the board')


def check_board(port, force):
    info = run_on(port, '''import sys, os, ujson
try:
    import plasma
    has_plasma = True
except ImportError:
    has_plasma = False
print({mark!r} + ujson.dumps({{'version': list(sys.implementation.version[:2]),
    'build': getattr(sys.implementation, '_build', ''), 'plasma': has_plasma, 'files': os.listdir('/')}}))
'''.format(mark=MARK))
    if info['build'] != FIRMWARE_BUILD or not info['plasma'] or tuple(info['version']) < MIN_FIRMWARE:
        sys.exit('{} needs Pimoroni\'s Plasma Stick 2040 W MicroPython {}.{} or later (it has {} {}): flash that '
                 'first'.format(port, *MIN_FIRMWARE, info['build'] or 'MicroPython', '.'.join(map(str, info['version']))))
    if info['files'] and not force:
        sys.exit('{} already has files ({}). It may be a board in use: --force replaces its setup.'.format(
            port, ', '.join(info['files'][:6])))
    print('Board on {}: {} MicroPython {}, {}'.format(port, info['build'], '.'.join(map(str, info['version'])),
                                                     'empty' if not info['files'] else 'replacing its setup'))


def capture_wifi(port):
    """The Wi-Fi network and password from a working board, decrypted there with its own key."""
    return run_on(port, '''import ujson
from deviceconfig import _load_provisioning_data
p = _load_provisioning_data()
print({mark!r} + ujson.dumps({{'ssid': p['ssid'], 'password': p['password']}}))
'''.format(mark=MARK))


def write_board(port, name, ssid, wifi_password, mqtt_password, config):
    result = run_on(port, '''import ujson
from wificrypto import encrypt_password, decrypt_password
cfg = ujson.loads({config!r})
wiv, wenc = encrypt_password({wifi!r})
with open('provisioning.json', 'w') as f:
    ujson.dump({{'device_name': {name!r}, 'ssid': {ssid!r}, 'password_iv': wiv, 'password_enc': wenc}}, f)
cfg['mqtt']['password_iv'], cfg['mqtt']['password_enc'] = encrypt_password({mqtt!r})
with open('config.json', 'w') as f:
    ujson.dump(cfg, f)
p, c = ujson.load(open('provisioning.json')), ujson.load(open('config.json'))
print({mark!r} + ujson.dumps({{'wifi': decrypt_password(p['password_iv'], p['password_enc']) == {wifi!r},
    'mqtt': decrypt_password(c['mqtt']['password_iv'], c['mqtt']['password_enc']) == {mqtt!r}}}))
'''.format(config=json.dumps(config), wifi=wifi_password, mqtt=mqtt_password, name=name, ssid=ssid, mark=MARK))
    if not (result['wifi'] and result['mqtt']):
        sys.exit('the passwords did not read back on the board: {}'.format(result))


def provision_mqtt(mosqman, profile, name):
    """Create the board's broker user (or give it a new password); returns the password."""
    r = subprocess.run([mosqman, '-p', profile, 'device', 'provision', name, '--json'], capture_output=True, text=True)
    result = json.loads(r.stdout or '{}')
    if r.returncode != 0 or not result.get('password'):
        sys.exit('Mosquitto Manager could not provision {} (exit {}): {}'.format(name, r.returncode, result.get('error')))
    password = result.pop('password')
    print('MQTT user {}: {}, reload {}{}'.format(name, 'created' if result.get('created') else 'new password',
                                                result.get('reload'),
                                                ''.join('\n  note: ' + w for w in result.get('warnings', []))))
    return password, result.get('reload')


def boot(port):
    """Restart the board and wait for Wi-Fi and the web server; returns its IP address."""
    r = subprocess.run([sys.executable, os.path.join(PROJECT_ROOT, 'scripts', 'device_log.py'), '--reset',
                        '--seconds', '90', '--quiet', '--port', port], capture_output=True, text=True)
    found = dict(re.findall(r'MILESTONE (\w+) ok ?(\S*)', r.stdout))
    for problem in re.findall(r'PROBLEM (?:traceback|error|memory) .*', r.stdout):
        print('  ' + problem)
    if 'setup_ap' in found or 'provisioning' in found:
        sys.exit('The board started setup mode: the Wi-Fi details did not take. See build/device.log')
    if 'wlan' not in found or 'http' not in found:
        sys.exit('The board did not reach Wi-Fi and its web server. See build/device.log')
    return found['wlan']


# --- Commands ------------------------------------------------------------------------------------

def cmd_save_wifi(args):
    if args.from_port:
        wifi = capture_wifi(args.from_port)
        subprocess.run([sys.executable, '-m', 'mpremote', 'connect', args.from_port, 'reset'], capture_output=True)
        print('Copied the Wi-Fi details for {!r} from the board on {} (restarted it).'.format(wifi['ssid'], args.from_port))
    else:
        wifi = {'ssid': input('Wi-Fi network (SSID): ').strip(), 'password': getpass.getpass('Wi-Fi password: ')}
    if not wifi['ssid'] or not wifi['password']:
        sys.exit('Both the network and the password are needed')
    save_wifi(wifi['ssid'], wifi['password'])
    print('Saved, encrypted for this Windows user, in', WIFI_FILE)


def cmd_setup(args):
    error = name_error(args.name)
    if error:
        sys.exit(error)
    template = read_template(args.template)
    wifi = load_wifi()
    if template.get('wifi_ssid') and template['wifi_ssid'] != wifi['ssid']:
        sys.exit('The template is for Wi-Fi {!r} but the saved details are for {!r}'.format(
            template['wifi_ssid'], wifi['ssid']))
    config = build_config(template, args.name, [
        ('device', 'device_led_count', args.leds),
        ('device', 'device_led_rgbw', args.rgbw),
        ('device', 'led_layout', args.layout),
        ('device', 'mode_parameters', {'Colour': args.colour} if args.colour else None),
        ('mqtt', 'group', args.group),
    ])
    check_board(args.port, args.force)

    print('Deploying the firmware...')
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                        os.path.join(PROJECT_ROOT, 'scripts', 'deploy.ps1'), '-SkipReset', '-Port', args.port],
                       capture_output=True, text=True)
    if 'DEPLOY_SUCCESS' not in r.stdout:
        sys.exit('The deploy failed:\n' + (r.stdout + r.stderr)[-800:])

    mosqman = template.get('mosqman', {})
    mqtt_password, reload = provision_mqtt(mosqman.get('exe', 'mosqman'), mosqman.get('profile', 'default'), args.name)
    write_board(args.port, args.name, wifi['ssid'], wifi['password'], mqtt_password, config)
    del mqtt_password, wifi
    print('Settings written; both passwords encrypted with the board\'s key.')

    ip = boot(args.port)
    print('{} is on Wi-Fi at {}, with its web page up: http://{}/'.format(args.name, ip, ip))
    if reload == 'manual':
        print('\nNext: restart Mosquitto so it loads the new user, then run\n'
              '  py scripts\\new_device.py check --name {} --host {}'.format(args.name, ip))


def cmd_check(args):
    if args.mqtt_user is None:
        args.mqtt_user = read_template(args.template).get('test_mqtt_user')
    print('Waiting for {} to log in to the broker...'.format(args.name))
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://{}/api/info'.format(args.host), timeout=8) as r:
                info = json.loads(r.read())
            if info['mqtt']['connected']:
                print('Connected ({} v{}).'.format(info['name'], info['version']))
                break
            error = info['mqtt']['error']
        except OSError as e:
            error = str(e)
        time.sleep(5)
    else:
        sys.exit('Not connected after {} s: {}'.format(args.timeout, error))
    smoke = [sys.executable, os.path.join(PROJECT_ROOT, 'scripts', 'smoke_test.py'), '--host', args.host]
    if args.mqtt_user:
        smoke += ['--mqtt-user', args.mqtt_user]
    sys.exit(subprocess.run(smoke).returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('save-wifi', help='Save the Wi-Fi details new boards get (encrypted for this Windows user)')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--from-port', help='Copy them from a working board on this serial port')
    source.add_argument('--prompt', action='store_true', help='Type them in')
    p.set_defaults(func=cmd_save_wifi)

    p = sub.add_parser('setup', help='Set up a freshly flashed board')
    p.add_argument('--port', required=True)
    p.add_argument('--name', required=True, help='Device name: also its hostname and MQTT user')
    p.add_argument('--template', default=TEMPLATE)
    p.add_argument('--leds', type=int, help='LED count')
    p.add_argument('--rgbw', action=argparse.BooleanOptionalAction, help='RGBW LEDs (--no-rgbw: RGB)')
    p.add_argument('--layout', choices=('bottle', 'horizontal', 'vertical'))
    p.add_argument('--colour', help='Default colour, #RRGGBB')
    p.add_argument('--group', help='MQTT group to join')
    p.add_argument('--force', action='store_true', help='Replace whatever is already on the board')
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser('check', help='After the broker reload: wait for MQTT, then run the smoke test')
    p.add_argument('--name', required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--mqtt-user', help="The smoke test's own MQTT login (default: the template's test_mqtt_user)")
    p.add_argument('--template', default=TEMPLATE)
    p.add_argument('--timeout', type=int, default=180)
    p.set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
