import machine
import ubinascii
from log import Log

TAG = "IDENT"


def generate_default_device_name():
    """Generate a stable default device name from Pico unique_id."""
    try:
        uid = machine.unique_id()
        uid_hex = ubinascii.hexlify(uid).decode('utf-8').upper()
        short_id = uid_hex[-6:]
        return f'PicoLights-{short_id}'
    except Exception as e:
        Log.warn(TAG, f'Failed to generate device name from unique_id: {e}')
        return 'PicoLights-UNKNOWN'


def sanitize_device_name(value):
    """Keep only safe MQTT/client-id characters, fallback to generated name."""
    if not value:
        return generate_default_device_name()

    value = value.strip().replace(' ', '-')
    filtered = []
    for ch in value:
        if ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or ch in ['-', '_']:
            filtered.append(ch)
    cleaned = ''.join(filtered).strip('-_')
    if not cleaned:
        return generate_default_device_name()
    return cleaned
