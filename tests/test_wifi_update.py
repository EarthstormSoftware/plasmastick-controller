"""The PC's side of an update over Wi-Fi (scripts/wifi_update.py): which files it sends and removes."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import wifi_update  # noqa: E402


def test_the_pc_sends_only_changed_files_and_removes_leftovers(tmp_path):
    for name, data in (('app.mpy', b'same'), ('api.mpy', b'changed'), ('main.py', b'changed stub')):
        (tmp_path / name).write_bytes(data)
    local = {name: str(tmp_path / name) for name in ('app.mpy', 'api.mpy', 'main.py')}
    # main.py and otaboot are USB only: never sent or removed, even if they differ
    installed = {'app.mpy': [4, hashlib.sha256(b'same').hexdigest()], 'api.mpy': [3, 'x'], 'old.mpy': [1, 'y'],
                 'main.py': [1, 'z'], 'otaboot.mpy': [1, 'w']}
    send, remove = wifi_update.plan(local, installed)
    assert [f['path'] for f in send] == ['api.mpy'] and send[0]['size'] == 7
    assert remove == ['old.mpy']
