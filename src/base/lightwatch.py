"""
The topic a light watches for alerts (base.lightmqtt; mqtt.alert_topic): loaded only when one is set.
"""
from base.compat import json

_OFF = ('', '0', 'off', 'false', 'no', 'clear', 'idle', 'closed', 'inactive')


def watched(text):
    """Whether a message on the watched topic means "now": a JSON object with "motion" (Pico PIR's state),
    "State" or "state" says so itself; true or ON does; false, OFF, 0 or empty doesn't; any other text does."""
    try:
        payload = json.loads(text)
    except ValueError:
        return text.strip().lower() not in _OFF
    if isinstance(payload, dict):
        for key in ('motion', 'State', 'state', 'occupancy'):
            if key in payload:
                value = payload[key]
                return value.strip().lower() not in _OFF if isinstance(value, str) else bool(value)
        return True
    if isinstance(payload, str):
        return payload.strip().lower() not in _OFF
    return bool(payload)


def topic_matches(pattern, topic):
    """Whether topic is one that the subscription pattern (with + and # wildcards) receives."""
    wanted, parts = pattern.split('/'), topic.split('/')
    for i, level in enumerate(wanted):
        if level == '#':
            return True
        if i >= len(parts) or (level != '+' and level != parts[i]):
            return False
    return len(parts) == len(wanted)
