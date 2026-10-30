"""
One-way hashing for the web password.

The device only ever needs to check the web password, never to read it back, so the settings keep a salted, repeated
SHA-256 hash rather than the password: "sha256$<iterations>$<salt hex>$<hash hex>". The iteration count is stored with
it, so it can be raised later without breaking existing hashes.
"""
from base.compat import hashlib, hexlify, unhexlify, urandom

ITERATIONS = 1000


def _digest(password, salt, iterations):
    pw = password.encode('utf-8')
    d = hashlib.sha256(salt + pw).digest()
    for _ in range(iterations - 1):
        d = hashlib.sha256(d + pw).digest()
    return d


def hash_password(password, iterations=ITERATIONS):
    salt = urandom(16)
    d = _digest(password, salt, iterations)
    return 'sha256${}${}${}'.format(iterations, hexlify(salt).decode(), hexlify(d).decode())


def check_password(password, stored):
    """True if password matches a hash from hash_password. Compares in constant time."""
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split('$')
        if scheme != 'sha256':
            return False
        expected = unhexlify(hash_hex)
        actual = _digest(password, unhexlify(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    if len(actual) != len(expected):
        return False
    diff = 0
    for a, b in zip(actual, expected):
        diff |= a ^ b
    return diff == 0
