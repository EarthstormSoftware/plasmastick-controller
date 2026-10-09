"""Smoke-test a running device over HTTP and MQTT (host-side, not deployed).

Checks the HTTP API and web UI, then publishes StateChange/ModeChange/BrightnessChange control
messages from this PC and confirms the device applied them through /api/info.
The device's original power state and mode are restored at the end.

The broker address, user and device name come from the device's /api/config.
That omits the password, so it is read from a local config.json (gitignored),
the PLASMASTICK_MQTT_PASSWORD environment variable, or --mqtt-password.

    py scripts/smoke_test.py --host PicoLights-A1B2C3.local
    py scripts/smoke_test.py --host 192.168.1.50 --soak 300

Prints PASS/FAIL/SKIP lines; exits 1 if anything failed, 2 if the device can't be reached.
"""
import argparse
import json
import os
import socket
import sys
import time
import base64
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def report(status, name, detail=''):
    results.append(status)
    print(f'{status} {name}' + (f' ({detail})' if detail else ''), flush=True)


class Device:
    def __init__(self, host, timeout, password=None):
        self.base = f'http://{host}'
        self.timeout = timeout
        self.password = password

    def request(self, method, path, body=None, headers=None):
        """Return (status, parsed JSON or text, headers, seconds)."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        if self.password:
            token = base64.b64encode(f'smoke-test:{self.password}'.encode()).decode()
            req.add_header('Authorization', f'Basic {token}')
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw, headers = resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            status, raw, headers = e.code, e.read(), e.headers
        elapsed = time.monotonic() - start
        text = raw.decode('utf-8', errors='replace')
        try:
            return status, json.loads(text), headers, elapsed
        except ValueError:
            return status, text, headers, elapsed

    def info(self):
        status, body, _, _ = self.request('GET', '/api/info')
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f'/api/info returned {status}')
        return body

    def wait_for(self, key, expected, timeout=5.0):
        """Poll /api/info until info[key] == expected. Returns (ok, last value)."""
        deadline = time.monotonic() + timeout
        value = None
        while time.monotonic() < deadline:
            value = self.info().get(key)
            if value == expected:
                return True, value
            time.sleep(0.25)
        return False, value


def check(name, ok, detail=''):
    report('PASS' if ok else 'FAIL', name, detail)
    return ok


def contains_key(obj, needle):
    if isinstance(obj, dict):
        # web.password_set only says whether one is set, so it doesn't count.
        return any((needle in k.lower() and not k.lower().endswith('_set')) or contains_key(v, needle)
                   for k, v in obj.items())
    if isinstance(obj, list):
        return any(contains_key(v, needle) for v in obj)
    return False


def test_http(dev):
    status, body, _, elapsed = dev.request('GET', '/api/info')
    check('http.info', status == 200 and isinstance(body, dict) and {'name', 'state', 'mode'} <= body.keys(),
          f'{status} in {elapsed * 1000:.0f} ms')

    status, body, headers, _ = dev.request('GET', '/')
    check('http.webui', status == 200 and 'text/html' in headers.get('Content-Type', ''), f'{status}')

    status, config, _, _ = dev.request('GET', '/api/config')
    check('http.config', status == 200 and isinstance(config, dict), f'{status}')
    if isinstance(config, dict):
        check('http.config_hides_passwords', not contains_key(config, 'password'))

    status, _, _, _ = dev.request('GET', '/no-such-page')
    check('http.not_found', status == 404, f'{status}')

    before = dev.info().get('state')
    status, _, _, _ = dev.request('POST', '/api/power/toggle', {}, headers={'Origin': 'http://evil.example'})
    check('http.cross_site_refused', status == 403 and dev.info().get('state') == before, f'{status}')

    password_set = isinstance(config, dict) and config.get('web', {}).get('password_set')
    if password_set:
        status, _, headers, _ = Device(dev.base[len('http://'):], dev.timeout).request('GET', '/api/info')
        check('http.password_required', status == 401 and 'Basic' in headers.get('WWW-Authenticate', ''), f'{status}')
    else:
        report('SKIP', 'http.password_required', 'no web password set on the device')

    for action, expected in (('off', False), ('on', True), ('toggle', False), ('toggle', True)):
        status, _, _, _ = dev.request('POST', f'/api/power/{action}', {})
        ok, value = dev.wait_for('state', expected)
        check(f'http.power_{action}', status == 200 and ok, f'state={value}')

    status, _, _, _ = dev.request('POST', '/api/mode', {'mode': 'Colour', 'parameters': {'Colour': '#00FF00'}})
    ok, value = dev.wait_for('mode', 'Colour')
    check('http.mode_colour', status == 200 and ok, f'mode={value}')

    status, _, _, _ = dev.request('POST', '/api/mode', {'mode': 'Rainbows'})
    ok, value = dev.wait_for('mode', 'Rainbows')
    check('http.mode_rainbows', status == 200 and ok, f'mode={value}')

    status, _, _, _ = dev.request('POST', '/api/mode', {'mode': 'NoSuchMode'})
    check('http.mode_invalid_rejected', status == 400 and dev.info().get('mode') == 'Rainbows', f'{status}')

    status, _, _, _ = dev.request('POST', '/api/brightness', {'brightness': 40})
    ok, value = dev.wait_for('brightness', 40)
    check('http.brightness', status == 200 and ok, f'brightness={value}')

    status, _, _, _ = dev.request('POST', '/api/brightness', {'brightness': 0})
    check('http.brightness_invalid_rejected', status == 400 and dev.info().get('brightness') == 40, f'{status}')

    return config if isinstance(config, dict) else {}


def load_mqtt_password(cli_password):
    if cli_password is not None:
        return cli_password
    if os.environ.get('PLASMASTICK_MQTT_PASSWORD'):
        return os.environ['PLASMASTICK_MQTT_PASSWORD']
    try:
        with open(os.path.join(PROJECT_ROOT, 'config.json'), encoding='utf-8') as fp:
            return json.load(fp).get('mqtt', {}).get('password') or None
    except (OSError, ValueError):
        return None


def test_mqtt(dev, config, args):
    mqtt = config.get('mqtt', {})
    if not mqtt.get('enabled') or not mqtt.get('broker_address'):
        report('SKIP', 'mqtt', 'MQTT is disabled or has no broker on the device')
        return
    try:
        import paho.mqtt.client as paho
    except ImportError:
        report('FAIL', 'mqtt', 'paho-mqtt is not installed: py -m pip install -r requirements-dev.txt')
        return

    broker = args.broker or mqtt['broker_address']
    device_name = config.get('device', {}).get('device_name')
    topic = f'/home/device/{device_name}/control'

    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f'smoke-test-{os.getpid()}')
    if mqtt.get('user'):
        client.username_pw_set(mqtt['user'], load_mqtt_password(args.mqtt_password))
    try:
        client.connect(broker, args.mqtt_port, keepalive=30)
    except OSError as e:
        report('FAIL', 'mqtt.connect', f'{broker}:{args.mqtt_port}: {e}')
        return
    client.loop_start()
    try:
        deadline = time.monotonic() + 10
        while not client.is_connected() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not check('mqtt.connect', client.is_connected(), f'{broker}:{args.mqtt_port}, topic {topic}'):
            report('FAIL', 'mqtt', 'broker refused the connection (check user/password)')
            return

        def publish(payload):
            text = payload if isinstance(payload, str) else json.dumps(payload)
            # Never retained, so nothing is left on the broker for the device to replay.
            client.publish(topic, text, qos=1, retain=False).wait_for_publish(5)

        for expected in (False, True):
            publish({'MessageType': 'StateChange', 'Parameters': {'State': expected}})
            ok, value = dev.wait_for('state', expected)
            check(f'mqtt.state_{"on" if expected else "off"}', ok, f'state={value}')

        publish({'MessageType': 'ModeChange', 'Parameters': {'Mode': 'Colour', 'ModeParameters': {'Colour': '#0000FF'}}})
        ok, value = dev.wait_for('mode', 'Colour')
        check('mqtt.mode_colour', ok, f'mode={value}')

        publish({'MessageType': 'ModeChange', 'Parameters': {'Mode': 'Rainbows'}})
        ok, value = dev.wait_for('mode', 'Rainbows')
        check('mqtt.mode_rainbows', ok, f'mode={value}')

        publish({'MessageType': 'BrightnessChange', 'Parameters': {'Brightness': 60}})
        ok, value = dev.wait_for('brightness', 60)
        check('mqtt.brightness', ok, f'brightness={value}')

        # Bad messages must be ignored without changing anything or breaking the handler.
        publish({'MessageType': 'ModeChange', 'Parameters': {'Mode': 'NoSuchMode'}})
        publish({'MessageType': 'StateChange', 'Parameters': {'State': 'yes'}})
        publish({'MessageType': 'BrightnessChange', 'Parameters': {'Brightness': 500}})
        publish('this is not json')
        publish({'MessageType': 'Unknown'})
        time.sleep(1)
        info = dev.info()
        check('mqtt.bad_messages_ignored',
              info.get('mode') == 'Rainbows' and info.get('state') is True and info.get('brightness') == 60,
              f'mode={info.get("mode")} state={info.get("state")} brightness={info.get("brightness")}')

        publish({'MessageType': 'StateChange', 'Parameters': {'State': False}})
        ok, value = dev.wait_for('state', False)
        check('mqtt.still_handling_after_bad_messages', ok, f'state={value}')
    finally:
        client.loop_stop()
        client.disconnect()


def soak(dev, seconds, max_latency):
    failures, latencies = 0, []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        # Each failed or slow poll is printed with the time, to match against the device log.
        stamp = time.strftime('%H:%M:%S')
        try:
            status, _, _, elapsed = dev.request('GET', '/api/info')
            latencies.append(elapsed)
            if status != 200:
                failures += 1
                print(f'SOAK_FAIL {stamp} HTTP {status}', flush=True)
            elif elapsed > max_latency:
                print(f'SOAK_SLOW {stamp} {elapsed * 1000:.0f} ms', flush=True)
        except OSError as e:
            failures += 1
            print(f'SOAK_FAIL {stamp} {e!r}', flush=True)
        time.sleep(5)
    worst = max(latencies) if latencies else 0
    check('soak', failures == 0 and worst <= max_latency,
          f'{len(latencies)} polls, {failures} failed, worst {worst * 1000:.0f} ms, limit {max_latency * 1000:.0f} ms')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', required=True, help='Device hostname or IP')
    parser.add_argument('--timeout', type=float, default=5, help='HTTP timeout in seconds (default 5)')
    parser.add_argument('--skip-mqtt', action='store_true')
    parser.add_argument('--broker', help="Override the broker address from the device's config")
    parser.add_argument('--mqtt-port', type=int, default=1883)
    parser.add_argument('--mqtt-password', help='MQTT password (default: local config.json or PLASMASTICK_MQTT_PASSWORD)')
    parser.add_argument('--password', default=os.environ.get('PLASMASTICK_WEB_PASSWORD'),
                        help='Web password, if one is set (default: PLASMASTICK_WEB_PASSWORD)')
    parser.add_argument('--soak', type=float, default=0, help='Poll /api/info for this many seconds afterwards')
    parser.add_argument('--max-latency', type=float, default=2.0, help='Soak latency limit in seconds (default 2)')
    args = parser.parse_args()

    try:
        ip = socket.gethostbyname(args.host)
    except OSError as e:
        print(f'UNREACHABLE cannot resolve {args.host}: {e}')
        return 2
    report('PASS', 'resolve', f'{args.host} -> {ip}')

    dev = Device(args.host, args.timeout, args.password)
    try:
        original = dev.info()
    except (OSError, RuntimeError) as e:
        print(f'UNREACHABLE {args.host}: {e}')
        return 2

    try:
        config = test_http(dev)
        if args.skip_mqtt:
            report('SKIP', 'mqtt', '--skip-mqtt')
        else:
            test_mqtt(dev, config, args)
    except (OSError, RuntimeError) as e:
        report('FAIL', 'aborted', f'device stopped responding: {e}')
    finally:
        # Mode parameters aren't exposed, so the mode comes back with its configured defaults.
        try:
            dev.request('POST', '/api/mode', {'mode': original.get('mode')})
            if original.get('brightness'):
                dev.request('POST', '/api/brightness', {'brightness': original['brightness']})
            dev.request('POST', f'/api/power/{"on" if original.get("state") else "off"}', {})
            print(f'RESTORED state={original.get("state")} mode={original.get("mode")} brightness={original.get("brightness")}')
        except OSError as e:
            print(f'RESTORE_FAILED {e}')

    if args.soak:
        soak(dev, args.soak, args.max_latency)

    failed = results.count('FAIL')
    print(f'RESULT {"FAIL" if failed else "PASS"} passed={results.count("PASS")} failed={failed} '
          f'skipped={results.count("SKIP")}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
