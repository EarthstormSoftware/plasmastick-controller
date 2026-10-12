"""Update a Plasma Stick's firmware over Wi-Fi (see src/ota.py for the device side).

Normally run by deploy.ps1 -WiFi <host>, which compiles the firmware and passes
the file list. Sends only the files that differ from the device's, plus the
firmware files the new version no longer has (to remove), then waits for the
device to restart and confirm the new version. If it doesn't start properly,
the device puts the old firmware back by itself.

The device must have a web password: it's read from PLASMASTICK_WEB_PASSWORD,
--password, or asked for.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = ('main.py', 'otaboot.mpy', 'otaboot.py', 'boot.py')  # as in src/ota.py


def project_version():
    with open(os.path.join(PROJECT_ROOT, 'src', 'version.py'), encoding='utf-8') as f:
        return re.search(r"VERSION\s*=\s*'([^']+)'", f.read()).group(1)


def mpy_cross_version():
    """The .mpy major version mpy-cross writes (6 for 'mpy v6.3')."""
    out = subprocess.run([sys.executable, '-m', 'mpy_cross', '--version'], capture_output=True, text=True).stdout
    return int(re.search(r'mpy v(\d+)', out).group(1))


def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def plan(local, installed):
    """What to send and remove. local: {device path: local file}; installed: the
    device's {path: [size, sha256]}. Returns (files to send as manifest entries, paths to remove)."""
    send = []
    for path, src in sorted(local.items()):
        if path in PROTECTED:
            continue
        digest = sha256(src)
        if installed.get(path, [None, None])[1] != digest:
            send.append({'path': path, 'size': os.path.getsize(src), 'sha256': digest, 'src': src})
    # A module's .py would shadow its .mpy, so a leftover one of either form goes.
    # main.py and otaboot are USB-only: never sent, never removed.
    remove = sorted(p for p in installed if p not in local and p not in PROTECTED)
    return send, remove


class Device:
    def __init__(self, host, password):
        self.base = f'http://{host}'
        token = base64.b64encode(f'update:{password}'.encode()).decode()
        self.headers = {'Authorization': f'Basic {token}'}

    def call(self, method, path, body=None, data=None, timeout=30):
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        elif data is not None:
            headers['Content-Type'] = 'application/octet-stream'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            text = e.read().decode('utf-8', 'replace')
            try:
                text = json.loads(text).get('error', text)
            except ValueError:
                pass
            raise SystemExit(f'{method} {path}: HTTP {e.code}: {text}')


def wait_for_restart(device, version, started, timeout=240):
    """Wait for the device to come back with a fresh uptime, then for the update's verdict."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            info = device.call('GET', '/api/info', timeout=5)
        except (OSError, SystemExit):
            continue
        if info['uptime_s'] > time.monotonic() - started + 5:
            continue  # not restarted yet
        status = device.call('GET', '/api/update', timeout=60)
        state = (status.get('state') or {}).get('state')
        if state == 'good' and info['version'] == version:
            return True, f'running {version}, update confirmed'
        if state == 'rolled_back':
            return False, f'the update did not start properly; the device rolled back to {info["version"]}'
    return False, 'the device did not come back in time; if the update failed it will roll back by itself'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', required=True)
    parser.add_argument('--files', required=True, help='JSON map of device path -> local file (deploy.ps1 writes it)')
    parser.add_argument('--password', default=os.environ.get('PLASMASTICK_WEB_PASSWORD'))
    args = parser.parse_args()
    password = args.password or getpass.getpass(f'Web password for {args.host}: ')
    with open(args.files, encoding='utf-8-sig') as f:
        local = json.load(f)

    device = Device(args.host, password)
    status = device.call('GET', '/api/update', timeout=60)
    version, mpy = project_version(), mpy_cross_version()
    if mpy != status['mpy']:
        raise SystemExit(f'mpy-cross writes .mpy v{mpy}, but the device loads v{status["mpy"]}: '
                         'install the matching mpy-cross, or update over USB')
    send, remove = plan(local, status['files'])
    print(f'Device runs {status["version"]}; updating to {version}: '
          f'{len(send)} file(s) to send, {len(remove)} to remove, {status["free"] // 1024} KB free')
    if not send and not remove:
        print('Nothing to update.')
        print('UPDATE_SUCCESS (no changes)')
        return

    device.call('POST', '/api/update/begin', {'version': version, 'mpy': mpy, 'remove': remove,
                                              'files': [{k: f[k] for k in ('path', 'size', 'sha256')} for f in send]})
    for f in send:
        with open(f['src'], 'rb') as src:
            device.call('PUT', '/api/update/file/' + f['path'], data=src.read())
        print(f'  sent {f["path"]} ({f["size"]} bytes)')
    for path in remove:
        print(f'  removing {path}')
    started = time.monotonic()
    device.call('POST', '/api/update/commit', {})
    print('Committed; the device is restarting to apply it...')
    ok, message = wait_for_restart(device, version, started)
    print(message)
    if not ok:
        raise SystemExit('UPDATE_FAILED')
    print('UPDATE_SUCCESS')


if __name__ == '__main__':
    main()
