import asyncio
import hashlib
import importlib
import io
import json
import os
import sys

import pytest

import ota

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import wifi_update  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A device filesystem in a temporary folder, for both ota and boot."""
    base = str(tmp_path).replace('\\', '/') + '/'
    import otaboot as boot
    for module in (ota, boot):
        monkeypatch.setattr(module, 'ROOT', base)
        monkeypatch.setattr(module, 'STAGE', base + 'update')
        monkeypatch.setattr(module, 'PREV', base + 'prev')
        monkeypatch.setattr(module, 'STATE', base + 'update_state.json')
    monkeypatch.setattr(ota, 'mpy_version', lambda: 6)
    write(tmp_path, 'app.mpy', b'old main')
    write(tmp_path, 'api.mpy', b'old api')
    write(tmp_path, 'gone.mpy', b'old module the update drops')
    write(tmp_path, 'www/index.html', b'<old page>')
    write(tmp_path, 'main.py', b'stub')
    write(tmp_path, 'otaboot.mpy', b'rollback')
    write(tmp_path, 'config.json', b'{}')
    return tmp_path


def write(base, path, data):
    full = os.path.join(base, *path.split('/'))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'wb') as f:
        f.write(data)


def read(base, path):
    try:
        with open(os.path.join(base, *path.split('/')), 'rb') as f:
            return f.read()
    except OSError:
        return None


def entry(path, data):
    return {'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class Stream:
    def __init__(self, data):
        self.buf = io.BytesIO(data)

    async def read(self, n):
        return self.buf.read(n)


NEW = {'app.mpy': b'new main', 'api.mpy': b'new api', 'effects.mpy': b'new module', 'www/index.html': b'<new page>'}


def stage(update=None, remove=('gone.mpy',)):
    update = NEW if update is None else update
    u = ota.Updater()
    assert u.begin({'version': '9.9.9', 'mpy': 6, 'remove': list(remove),
                    'files': [entry(p, d) for p, d in update.items()]}) is None
    for path, data in update.items():
        assert asyncio.run(u.receive(path, Stream(data), len(data))) is None
    assert u.commit() is None
    return u


def boot_once():
    import otaboot as boot
    boot.run()
    return boot


@pytest.mark.parametrize('path', ['app.mpy', 'api.mpy', 'lib/microdot.mpy', 'lib/mqtt_as/__init__.mpy', 'www/index.html'])
def test_firmware_paths_are_allowed(path):
    assert ota.check_path(path) is None


@pytest.mark.parametrize('path', ['main.py', 'otaboot.mpy', 'boot.py', 'config.json', 'provisioning.json', '/app.mpy', '../app.mpy',
                                  'lib/../config.json', 'www/.hidden.html', 'secret/x.mpy', 'api.txt', '', None])
def test_everything_else_is_refused(path):
    assert ota.check_path(path) is not None


def test_a_manifest_is_checked(root):
    u = ota.Updater()
    assert 'v6' in u.begin({'version': '1', 'mpy': 5, 'files': []})
    assert u.begin({'version': '1', 'mpy': 6, 'files': [entry('config.json', b'x')]}) == 'config.json: not a firmware file'
    assert u.begin({'version': '1', 'mpy': 6, 'files': [], 'remove': ['otaboot.mpy']}) == 'otaboot.mpy: only updated over USB'
    assert u.begin({'version': '1', 'mpy': 6, 'files': [entry('main.py', b'x')]}) == 'main.py: only updated over USB'
    assert 'not enough space' in u.begin({'version': '1', 'mpy': 6,
                                         'files': [{'path': 'a.mpy', 'size': 10 ** 7, 'sha256': ''}]})


def test_a_corrupt_or_short_upload_is_refused(root):
    u = ota.Updater()
    assert u.begin({'version': '1', 'mpy': 6, 'files': [entry('api.mpy', b'right')]}) is None
    assert asyncio.run(u.receive('api.mpy', Stream(b'wrong'), 5)) == 'api.mpy: SHA-256 does not match'
    assert asyncio.run(u.receive('api.mpy', Stream(b'rig'), 5)) == 'api.mpy: upload cut short'
    assert asyncio.run(u.receive('app.mpy', Stream(b'x'), 1)) == 'app.mpy: not in the manifest'
    assert u.commit() == 'not received: api.mpy'
    assert read(root, 'update/ready') is None


def test_installed_files_lists_firmware_only(root):
    files = ota.installed_files()
    assert set(files) == {'app.mpy', 'api.mpy', 'gone.mpy', 'www/index.html'}  # not main.py, otaboot or config.json
    assert files['api.mpy'] == [7, hashlib.sha256(b'old api').hexdigest()]


def test_nothing_happens_without_a_staged_update(root):
    boot_once()
    assert read(root, 'app.mpy') == b'old main' and ota.state() is None


def test_an_update_is_applied_on_trial_then_confirmed(root):
    stage()
    boot = boot_once()
    for path, data in NEW.items():
        assert read(root, path) == data
    assert read(root, 'gone.mpy') is None
    assert read(root, 'prev/app.mpy') == b'old main' and read(root, 'prev/gone.mpy') == b'old module the update drops'
    assert read(root, 'config.json') == b'{}' and read(root, 'main.py') == b'stub'
    assert read(root, 'otaboot.mpy') == b'rollback'
    assert ota.state()['state'] == 'trial' and ota.state()['boots'] == 1
    assert boot._trial_timer.period == boot.DEADLINE_MS
    assert ota.mark_good() == '9.9.9'
    assert ota.state() == {'state': 'good', 'version': '9.9.9'}
    boot_once()  # later boots leave it alone
    assert read(root, 'app.mpy') == b'new main' and ota.state()['state'] == 'good'


def test_an_update_that_never_confirms_is_rolled_back(root):
    stage()
    boot_once()
    boot_once()  # the deadline restarted it: second trial boot
    assert ota.state()['boots'] == 2
    boot_once()  # third: give up
    assert ota.state() == {'state': 'rolled_back', 'version': '9.9.9'}
    assert read(root, 'app.mpy') == b'old main' and read(root, 'api.mpy') == b'old api'
    assert read(root, 'www/index.html') == b'<old page>' and read(root, 'gone.mpy') == b'old module the update drops'
    assert read(root, 'effects.mpy') is None  # new in the update: gone again
    assert ota.mark_good() is None


def test_the_deadline_only_restarts_an_unconfirmed_trial(root, monkeypatch):
    import machine
    resets = []
    monkeypatch.setattr(machine, 'reset', lambda: resets.append(1))
    stage()
    boot = boot_once()
    ota.mark_good()
    boot._deadline(None)
    assert resets == []
    stage({'app.mpy': b'newer'})
    boot = boot_once()
    boot._deadline(None)
    assert resets == [1]


def test_applying_resumes_after_a_power_cut(root):
    stage()
    import otaboot as boot
    m = json.loads(read(root, 'update/manifest.json'))
    # Cut off after the first file moved.
    boot._save(boot.STATE, {'state': 'applying', 'version': m['version'], 'files': m['files'], 'remove': m['remove']})
    first = m['files'][0]
    assert first == 'app.mpy'
    boot._move(boot.ROOT + first, boot.PREV + '/' + first)
    boot._move(boot.STAGE + '/files/' + first, boot.ROOT + first)
    os.remove(os.path.join(root, 'update', 'ready'))
    boot_once()
    for path, data in NEW.items():
        assert read(root, path) == data
    assert read(root, 'prev/' + first) == {'app.mpy': b'old main', 'api.mpy': b'old api',
                                           'www/index.html': b'<old page>', 'effects.mpy': None}[first]
    assert ota.state()['state'] == 'trial'


def test_a_new_update_replaces_the_backups(root):
    stage()
    boot_once()
    ota.mark_good()
    stage({'api.mpy': b'newest api'}, remove=())
    boot_once()
    assert read(root, 'prev/api.mpy') == b'new api' and read(root, 'prev/app.mpy') is None


def test_the_pc_sends_only_changed_files_and_removes_leftovers(tmp_path):
    for name, data in (('app.mpy', b'same'), ('api.mpy', b'changed'), ('main.py', b'changed stub')):
        (tmp_path / name).write_bytes(data)
    local = {name: str(tmp_path / name) for name in ('app.mpy', 'api.mpy', 'main.py')}
    # main.py and otaboot are USB only: never sent or removed, even if they differ.
    installed = {'app.mpy': [4, hashlib.sha256(b'same').hexdigest()], 'api.mpy': [3, 'x'], 'old.mpy': [1, 'y'],
                 'main.py': [1, 'z'], 'otaboot.mpy': [1, 'w']}
    send, remove = wifi_update.plan(local, installed)
    assert [f['path'] for f in send] == ['api.mpy'] and send[0]['size'] == 7
    assert remove == ['old.mpy']


def test_a_rollback_is_reported_once(root):
    stage()
    for _ in range(3):
        boot_once()
    assert ota.take_rollback() == '9.9.9'
    assert ota.take_rollback() is None
    assert ota.state()['state'] == 'rolled_back'


def test_on_trial_only_while_an_update_awaits_confirmation(root, monkeypatch):
    import otaboot
    monkeypatch.setattr(otaboot, '_trial_timer', None)
    boot_once()
    assert not otaboot.on_trial()
    stage()
    boot_once()
    assert otaboot.on_trial()
