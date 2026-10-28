"""
The settings, kept as JSON (config.json) and changed by GET and POST /api/config.

A product describes its settings as sections of Setting, next to the base's own (BASE_SETTINGS): each has a default,
a check, whether changing it needs a restart, and whether it's a secret (stored as given by `store`, such as a hash,
and never shown: the view says only whether it's set). The file keeps only what's known: anything else in it is
dropped, and anything missing has its default.

A platform whose file has its own layout (a Pico's, as the firmware before the base wrote it) gives a codec:
load(stored) turns the file into {section: {key: value}}, and dump(values) turns that back. A secret kept in a form that
has to be undone to use it (encrypted, not hashed) has a reveal, used through Settings.reveal().
"""
from base.compat import json, write_atomically
from base.log import Log

TAG = 'SETTINGS'


class Setting:
    def __init__(self, default, check=None, restart=False, secret=False, store=None, reveal=None):
        self.default = default
        self.check = check  # check(value): why it can't be used, or None
        self.restart = restart
        self.secret = secret
        self.store = store  # store(value): what's kept (a secret's hash); None (JSON null) clears a secret
        self.reveal = reveal  # reveal(kept): the secret itself, from what's kept


def is_int(low, high):
    def check(value):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            return 'must be a whole number, {}-{}'.format(low, high)
        return None
    return check


def is_bool(value):
    return None if isinstance(value, bool) else 'must be true or false'


def is_one_of(*choices):
    def check(value):
        return None if value in choices else 'must be one of ' + ', '.join(str(c) for c in choices)
    return check


def is_text(longest, allow_empty=True):
    def check(value):
        if not isinstance(value, str) or len(value) > longest or (not allow_empty and not value.strip()):
            return 'must be text, up to {} characters'.format(longest)
        return None
    return check


def _check_password(value):
    return None if isinstance(value, str) and 0 < len(value) <= 64 else 'must be 1-64 characters'


def _hash(value):
    from base.passwords import hash_password
    return hash_password(value)


# Every product has these
BASE_SETTINGS = {
    'device': {
        # 0: warnings and errors, 1: and info, 2: and debug
        'log_level': Setting(1, is_int(0, 2)),
    },
    'web': {
        # Optional: with one, the page and API need it (HTTP Basic auth, any username)
        'password': Setting('', _check_password, secret=True, store=_hash),
    },
}


def merge(*schemas):
    merged = {}
    for schema in schemas:
        for section, settings in schema.items():
            merged.setdefault(section, {}).update(settings)
    return merged


class Settings:
    def __init__(self, path, schema, codec=None):
        self.path = path
        self.schema = schema
        self.codec = codec
        self.values = self._defaults()
        try:
            with open(path) as f:
                stored = json.loads(f.read())
            if not isinstance(stored, dict):
                raise ValueError('not a JSON object')
            if codec is not None:
                stored = codec.load(stored)
            for section, settings in schema.items():
                for key, setting in settings.items():
                    value = stored.get(section, {}).get(key)
                    if value is None:
                        continue
                    if setting.secret or setting.check is None or setting.check(value) is None:
                        self.values[section][key] = value
                    else:
                        Log.warn(TAG, '{}.{} in {} {}: using {}'.format(section, key, path, setting.check(value),
                                                                        setting.default))
        except OSError:
            Log.info(TAG, 'No {} yet: using the defaults'.format(path))
        except ValueError as e:
            Log.error(TAG, '{} is damaged ({}): using the defaults'.format(path, e))

    def _defaults(self):
        return {section: {key: setting.default for key, setting in settings.items()}
                for section, settings in self.schema.items()}

    def get(self, section, key):
        return self.values[section][key]

    def reveal(self, section, key):
        """A secret as it's used (decrypted), where it's kept in another form; otherwise as get()."""
        value = self.values[section][key]
        reveal = self.schema[section][key].reveal
        return reveal(value) if reveal is not None and value else value

    def view(self):
        """The settings for the page and GET /api/config: a secret only as whether it's set (<key>_set)."""
        shown = {}
        for section, settings in self.schema.items():
            shown[section] = {}
            for key, setting in settings.items():
                if setting.secret:
                    shown[section][key + '_set'] = bool(self.values[section][key])
                else:
                    shown[section][key] = self.values[section][key]
        return shown

    def update(self, updates):
        """Checks and saves updates ({section: {key: value}}), all or none. Returns (error, changed), where error is
        why they weren't saved (or None) and changed is the (section, key) pairs whose value changed."""
        if not isinstance(updates, dict):
            return 'Send a JSON object of settings', set()
        new = {}
        for section, values in updates.items():
            settings = self.schema.get(section)
            if settings is None or not isinstance(values, dict):
                return 'Unknown settings section: {}'.format(section), set()
            for key, value in values.items():
                setting = settings.get(key)
                if setting is None:
                    return 'Unknown setting: {}.{}'.format(section, key), set()
                if setting.secret and value is None:
                    new[(section, key)] = ''
                    continue
                error = setting.check(value) if setting.check else None
                if error:
                    return '{}.{} {}'.format(section, key, error), set()
                new[(section, key)] = setting.store(value) if setting.store else value
        changed = set(name for name, value in new.items() if self.values[name[0]][name[1]] != value)
        if changed:
            # Saved before they're used, so a failed save changes nothing
            values = {section: dict(settings) for section, settings in self.values.items()}
            for section, key in changed:
                values[section][key] = new[(section, key)]
            write_atomically(self.path, self._text(values))
            self.values = values
        return None, changed

    def restart_needed(self, changed):
        return any(self.schema[section][key].restart for section, key in changed)

    def reset(self, keep=()):
        """Puts everything back to its default, except the (section, key) pairs in keep."""
        kept = {name: self.values[name[0]][name[1]] for name in keep}
        self.values = self._defaults()
        for (section, key), value in kept.items():
            self.values[section][key] = value
        self.save()

    def save(self):
        write_atomically(self.path, self._text(self.values))

    def _text(self, values):
        return json.dumps(self.codec.dump(values) if self.codec is not None else values)
