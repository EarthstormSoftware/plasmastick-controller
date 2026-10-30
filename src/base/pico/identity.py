"""The device's name: the MQTT client id and the network name (<name>.local).

It's the one set on the page (device_name.json), else the one given in setup mode (provisioning.json), else
config.json's device.device_name, else made from the board's unique id with the product's prefix.
"""
import machine
from base.compat import json, hexlify

NAME_FILE = 'device_name.json'


def default_name(prefix):
    """A name that stays the same for this board: the prefix and the end of its unique id."""
    return prefix + hexlify(machine.unique_id()).decode().upper()[-6:]


def clean(value, prefix):
    """value with only the characters a client id and a host name can have, or the default name if that leaves none."""
    value = (value or '').strip().replace(' ', '-')
    cleaned = ''.join(c for c in value if 'a' <= c <= 'z' or 'A' <= c <= 'Z' or '0' <= c <= '9' or c in '-_')
    return cleaned.strip('-_') or default_name(prefix)


def renamed(path):
    """The name set on the page, or None."""
    try:
        with open(path) as f:
            return json.loads(f.read()).get('name')
    except (OSError, ValueError, AttributeError):
        return None
