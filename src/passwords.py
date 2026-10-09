"""
One-way hashing for the web password.

The device only ever needs to check the web password, never to read it back,
so config.json keeps a salted, repeated SHA-256 hash rather than the password:
"sha256$<iterations>$<salt hex>$<hash hex>". The iteration count is stored with
it, so it can be raised later without breaking existing hashes.

(The MQTT and Wi-Fi passwords have to be sent on, so they're encrypted instead:
see wificrypto.py.)
"""
import os
import uhashlib
import ubinascii

ITERATIONS = 1000


def _digest(password, salt, iterations):
    pw = password.encode('utf-8')
    d = uhashlib.sha256(salt + pw).digest()
    for _ in range(iterations - 1):
        d = uhashlib.sha256(d + pw).digest()
    return d


def hash_password(password, iterations=ITERATIONS):
    salt = os.urandom(16)
    d = _digest(password, salt, iterations)
    return 'sha256${}${}${}'.format(iterations, ubinascii.hexlify(salt).decode(), ubinascii.hexlify(d).decode())


def check_password(password, stored):
    """True if password matches a hash from hash_password. Compares in constant time."""
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split('$')
        if scheme != 'sha256':
            return False
        expected = ubinascii.unhexlify(hash_hex)
        actual = _digest(password, ubinascii.unhexlify(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    if len(actual) != len(expected):
        return False
    diff = 0
    for a, b in zip(actual, expected):
        diff |= a ^ b
    return diff == 0
