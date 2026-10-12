# Applies a firmware update staged over Wi-Fi (see ota.py), and rolls it back
# if the new firmware doesn't start properly. main.py calls run() before
# anything else.
#
# Self-contained on purpose: it imports nothing an update can replace, and,
# like main.py, is only ever updated over USB, so a bad update can't break the
# rollback.
#
# Files (all on the device's root):
#   /update/manifest.json, /update/files/..., /update/ready  a staged update
#   /prev/...                    the files it replaced or removed
#   /update_state.json           {"state": "applying" | "trial" | "good" | "rolled_back", ...}
#
# After applying, the firmware gets TRIAL_BOOTS boots, of DEADLINE_MS each, to
# mark itself good (ota.mark_good(), once its web server is up). Each boot
# without that, or a crash that leaves it in the REPL, counts as a failure: the
# deadline timer restarts the board, and after the last one the old files go back.
import os
import json
import machine

ROOT = '/'  # the firmware's folder (tests point it elsewhere)
STAGE = ROOT + 'update'
PREV = ROOT + 'prev'
STATE = ROOT + 'update_state.json'
TRIAL_BOOTS = 2
DEADLINE_MS = 180000


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _makedirs(path):
    # Every folder above path, outermost first.
    parts = path.split('/')[:-1]
    for i in range(len(parts)):
        folder = '/'.join(parts[:i + 1])
        if folder and parts[i] and not _exists(folder):
            os.mkdir(folder)


def _move(src, dst):
    _makedirs(dst)
    if _exists(dst):
        os.remove(dst)
    os.rename(src, dst)


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(path, data):
    with open(path, 'w') as f:
        json.dump(data, f)


def _rmtree(path):
    if not _exists(path):
        return
    for entry in os.ilistdir(path):
        child = path + '/' + entry[0]
        if entry[1] == 0x4000:  # a directory
            _rmtree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def _apply(m):
    # Safe to repeat after a power cut: a file already moved is skipped.
    _save(STATE, {'state': 'applying', 'version': m['version'], 'files': m['files'], 'remove': m['remove']})
    for path in m['files']:
        staged = STAGE + '/files/' + path
        if _exists(staged):
            if _exists(ROOT + path) and not _exists(PREV + '/' + path):
                _move(ROOT + path, PREV + '/' + path)
            _move(staged, ROOT + path)
    for path in m['remove']:
        if _exists(ROOT + path) and not _exists(PREV + '/' + path):
            _move(ROOT + path, PREV + '/' + path)
    _save(STATE, {'state': 'trial', 'boots': 0, 'version': m['version'], 'files': m['files'], 'remove': m['remove']})
    _rmtree(STAGE)
    print('otaboot: update to', m['version'], 'applied')


def _rollback(st):
    for path in st['files']:
        if _exists(PREV + '/' + path):
            _move(PREV + '/' + path, ROOT + path)
        elif _exists(ROOT + path):
            os.remove(ROOT + path)  # new in the update
    for path in st['remove']:
        if _exists(PREV + '/' + path):
            _move(PREV + '/' + path, ROOT + path)
    _save(STATE, {'state': 'rolled_back', 'version': st['version']})
    print('otaboot: update to', st['version'], 'did not start; rolled back')


def _deadline(_timer):
    st = _load(STATE)
    if st and st.get('state') == 'trial':
        print('otaboot: the updated firmware did not confirm in time; restarting')
        machine.reset()


_trial_timer = None


def _run():
    global _trial_timer
    st = _load(STATE)
    if _exists(STAGE + '/ready'):
        _apply(_load(STAGE + '/manifest.json'))
        st = _load(STATE)
    elif st and st.get('state') == 'applying':
        m = _load(STAGE + '/manifest.json')
        if m:
            _apply(m)  # finish what a power cut interrupted
            st = _load(STATE)
        else:
            _rollback(st)
            return
    if st and st.get('state') == 'trial':
        st['boots'] += 1
        if st['boots'] > TRIAL_BOOTS:
            _rollback(st)
            return
        _save(STATE, st)
        _trial_timer = machine.Timer(mode=machine.Timer.ONE_SHOT, period=DEADLINE_MS, callback=_deadline)


def on_trial():
    """True if this boot is an update's trial (its deadline timer is running)."""
    return _trial_timer is not None


def run():
    try:
        _run()
    except Exception as e:  # never stop the firmware starting
        print('otaboot: update handling failed:', repr(e))
