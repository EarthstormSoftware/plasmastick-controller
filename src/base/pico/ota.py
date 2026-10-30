"""Firmware updates over Wi-Fi: receiving and staging them.

A product's scripts/wifi_update.py drives it through the HTTP API:
  GET  /api/update          the files on the device (with SHA-256), MicroPython's
                            .mpy version, free space and the last update's state
  POST /api/update/begin    a manifest: {"version", "mpy", "files": [{"path",
                            "size", "sha256"}], "remove": [paths]}; or, as GET /api/update's
                            "manifest_lines" says it can, the same a line at a time
                            (application/x-ndjson): {"version", "mpy"}, then {"path", "size",
                            "sha256"} or {"remove": path} per line, so no block of several KB is
                            needed to read it (base.pico.web)
  PUT  /api/update/file/<path>   each file in the manifest, as the raw body
  POST /api/update/commit   marks the update ready; the device then restarts

otaboot.py applies a ready update at the next boot and rolls it back if the
new firmware doesn't call mark_good() in time. Only firmware files can be sent
(see check_path): never main.py or otaboot (they do the rollback), settings or
credentials.
"""
import os
import sys
import ujson
import uhashlib
import ubinascii

ROOT = '/'  # the firmware's folder (tests point it elsewhere)
STAGE = ROOT + 'update'
PREV = ROOT + 'prev'
STATE = ROOT + 'update_state.json'

# Where firmware files live, and what they can be.
# The base's own folders too: the firmware before the base could only write the first four, so a device moves to the
# base through a release whose only change is this list
DIRS = ('', 'lib', 'lib/mqtt_as', 'www', 'base', 'base/www', 'base/pico')
EXTENSIONS = ('.mpy', '.py', '.html', '.css', '.js', '.gz')  # .gz: the web files, stored gzipped
PROTECTED = ('main.py', 'otaboot.mpy', 'otaboot.py', 'boot.py')  # USB only: they run first and do the rollback
_NAME_CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.'
CHUNK = 512


def mpy_version():
    """The .mpy format this MicroPython loads (6 for v6.x)."""
    return getattr(sys.implementation, '_mpy', 0) & 0xFF


def check_path(path):
    """None if path names a firmware file an update may write or remove, else why not."""
    if not isinstance(path, str) or not path or path.startswith('/'):
        return 'bad path'
    directory, _, name = path.rpartition('/')
    if directory not in DIRS:
        return path + ': not a firmware folder'
    if not name or name[0] == '.' or any(c not in _NAME_CHARS for c in name):
        return path + ': bad file name'
    if name in PROTECTED and not directory:
        return path + ': only updated over USB'
    if not any(name.endswith(ext) for ext in EXTENSIONS):
        return path + ': not a firmware file'
    return None


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


def _rmtree(path):
    if not _exists(path):
        return
    for entry in os.ilistdir(path):
        child = path + '/' + entry[0]
        if entry[1] == 0x4000:
            _rmtree(child)
        else:
            os.remove(child)
    os.rmdir(path)


def _sha256(path):
    h = uhashlib.sha256()
    buf = bytearray(CHUNK)
    with open(path, 'rb') as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            h.update(memoryview(buf)[:n])
    return ubinascii.hexlify(h.digest()).decode()


def installed_files():
    """{path: [size, sha256]} for every firmware file on the device."""
    files = {}
    for directory in DIRS:
        try:
            entries = list(os.ilistdir(ROOT + directory))
        except OSError:
            continue
        for entry in entries:
            if entry[1] == 0x4000:
                continue
            path = (directory + '/' if directory else '') + entry[0]
            if check_path(path) is None:
                files[path] = [os.stat(ROOT + path)[6], _sha256(ROOT + path)]
    return files


def space_needed(files, remove):
    """The flash an update takes at its peak, in whole blocks: littlefs gives every file at least one block and every
    folder two. While otaboot applies it, the staged files are renamed into place (no new space), but the staging
    folders still stand while any new folders and the backups' folders (prev/...) are made."""
    try:
        block = os.statvfs(ROOT)[0]
    except AttributeError:  # not on Windows, where the tests run
        block = 4096
    total = sum(max(1, -(-f['size'] // block)) for f in files) * block
    folders = set()
    for f in files:
        folder = f['path'].rpartition('/')[0]
        folders.add('update/files/' + folder)
        if not _exists(ROOT + folder):
            folders.add(folder)
    for path in [f['path'] for f in files] + remove:
        if _exists(ROOT + path):
            folders.add('prev/' + path.rpartition('/')[0])
    # every folder named, and those above it (update, update/files, prev, base above base/www, ...)
    every = set()
    for folder in folders:
        parts = folder.rstrip('/').split('/')
        for i in range(len(parts)):
            if parts[i]:
                every.add('/'.join(parts[:i + 1]))
    return total + len(every) * 2 * block + 16384


def free_bytes():
    s = os.statvfs(ROOT)
    return s[0] * s[3]


def state():
    try:
        with open(STATE) as f:
            return ujson.load(f)
    except (OSError, ValueError):
        return None


def take_rollback():
    """The version rolled back, the first time it's asked after a rollback; else None."""
    st = state()
    if not st or st.get('state') != 'rolled_back' or st.get('reported'):
        return None
    st['reported'] = True
    with open(STATE, 'w') as f:
        ujson.dump(st, f)
    return st['version']


def mark_good():
    """Called once the firmware is running properly: confirms an update on trial.
    Returns the version confirmed, or None if there was nothing to confirm."""
    st = state()
    if not st or st.get('state') != 'trial':
        return None
    with open(STATE, 'w') as f:
        ujson.dump({'state': 'good', 'version': st['version']}, f)
    return st['version']


class Updater:
    def __init__(self):
        self.manifest = None
        self.received = set()

    def begin(self, m):
        """Check a manifest and get ready to receive its files. Returns an error or None."""
        if not isinstance(m, dict) or not isinstance(m.get('version'), str):
            return 'manifest needs a version'
        if m.get('mpy') != mpy_version():
            return 'files are for .mpy v{}, this MicroPython loads v{}'.format(m.get('mpy'), mpy_version())
        files, remove = m.get('files'), m.get('remove', [])
        if not isinstance(files, list) or not isinstance(remove, list):
            return 'manifest needs files and remove lists'
        for f in files:
            if not isinstance(f, dict):
                return 'bad file entry'
            error = check_path(f.get('path'))
            if error:
                return error
            if not isinstance(f.get('size'), int) or f['size'] < 0 or not isinstance(f.get('sha256'), str):
                return f['path'] + ': needs size and sha256'
        for path in remove:
            error = check_path(path)
            if error:
                return error
        # The last update's staging and backups go first: they'd count against this one
        _rmtree(STAGE)
        _rmtree(PREV)  # the previous update's backups: this one replaces them
        need = space_needed(files, remove)
        if need > free_bytes():
            return 'not enough space: needs {} KB, {} KB free'.format(need // 1024, free_bytes() // 1024)
        os.mkdir(STAGE)
        self.manifest = {'version': m['version'], 'files': {f['path']: f for f in files}, 'remove': remove}
        self.received = set()
        return None

    async def receive(self, path, stream, length):
        """Stream one file into the staging area, checking its size and hash. Returns an error or None."""
        if self.manifest is None:
            return 'no update started'
        f = self.manifest['files'].get(path)
        if f is None:
            return path + ': not in the manifest'
        if length != f['size']:
            return path + ': expected {} bytes, got {}'.format(f['size'], length)
        dest = STAGE + '/files/' + path
        _makedirs(dest)
        h = uhashlib.sha256()
        left = length
        with open(dest, 'wb') as out:
            while left:
                chunk = await stream.read(min(CHUNK, left))
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                left -= len(chunk)
        if left or ubinascii.hexlify(h.digest()).decode() != f['sha256']:
            os.remove(dest)
            return path + ': ' + ('upload cut short' if left else 'SHA-256 does not match')
        self.received.add(path)
        return None

    def commit(self):
        """Mark the update ready for boot.py. Returns an error or None."""
        if self.manifest is None:
            return 'no update started'
        missing = [p for p in self.manifest['files'] if p not in self.received]
        if missing:
            return 'not received: ' + ', '.join(missing)
        with open(STAGE + '/manifest.json', 'w') as f:
            ujson.dump({'version': self.manifest['version'], 'files': list(self.manifest['files']),
                        'remove': self.manifest['remove']}, f)
        with open(STAGE + '/ready', 'w') as f:
            f.write('1')
        self.manifest = None
        return None
