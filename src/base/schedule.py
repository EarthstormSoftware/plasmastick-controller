"""
The schedule: up to 8 events, each on chosen days at a time of day, or at sunrise or sunset plus or minus some
minutes, as PlasmaStick's:

  {"days": [0, 1, 2, 3, 4], "at": "18:30", "action": "on", "mode": "Rainbows", "brightness": 60}
  {"days": [5, 6], "at": "sunset", "offset": 30, "action": "on"}
  {"days": [0, 1, 2, 3, 4, 5, 6], "at": "23:00", "action": "off"}

days: 0 Monday ... 6 Sunday. at: "HH:MM" local time, "sunrise" or "sunset" (which need the location). mode and
brightness are optional, for "on". A light's events are the light's (on, off, a mode, a brightness), done like any
other change, so a group follows them. Another product has a schedule if it says what on and off mean
(scheduled(device, on): Pico PIR's sensing, say), without mode or brightness. Once the clock is right after a start,
the latest event's action is done, so a power cut doesn't leave things wrong.

The clock is the platform's: platform.local_time() gives (local seconds since 1970, or None until the clock is
right). Settings: schedule.events, schedule.latitude and schedule.longitude. Running the events is
base.scheduler's, loaded only while there are some.
"""
from base.compat import asyncio, release
from base.settings import Setting

TAG = 'SCHEDULE'
MAX_EVENTS = 8
SUN = ('sunrise', 'sunset')
CHECK_S = 15


def event_error(ev, modes):
    """None if ev is a usable event, else why not."""
    if not isinstance(ev, dict):
        return 'each event must be an object'
    days = ev.get('days')
    if not isinstance(days, list) or not days or any(isinstance(d, bool) or d not in range(7) for d in days):
        return 'days must list 0 (Monday) to 6 (Sunday)'
    at = ev.get('at')
    if at not in SUN:
        if not isinstance(at, str) or len(at) != 5 or at[2] != ':' or not (at[:2] + at[3:]).isdigit() \
                or int(at[:2]) > 23 or int(at[3:]) > 59:
            return 'at must be HH:MM, sunrise or sunset'
    offset = ev.get('offset', 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or not -180 <= offset <= 180:
        return 'offset must be -180 to 180 minutes'
    if ev.get('action') not in ('on', 'off'):
        return 'action must be on or off'
    if ev.get('mode') is not None and ev['mode'] not in modes:
        return 'unknown mode'
    b = ev.get('brightness')
    if b is not None and (isinstance(b, bool) or not isinstance(b, int) or not 1 <= b <= 100):
        return 'brightness must be 1-100'
    return None


def schedule_settings(modes):
    """The schedule's settings, for a light with these modes."""
    def events_error(events):
        if not isinstance(events, list):
            return 'must be a list'
        if len(events) > MAX_EVENTS:
            return 'can have at most {} events'.format(MAX_EVENTS)
        for n, ev in enumerate(events):
            error = event_error(ev, modes)
            if error:
                return 'event {}: {}'.format(n + 1, error)
        return None

    def degrees_error(limit):
        def check(value):
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not -limit <= value <= limit:
                return 'must be a number, -{0} to {0}'.format(limit)
            return None
        return check

    return {'schedule': {
        'events': Setting([], events_error),
        # For sunrise and sunset; north and east are positive
        'latitude': Setting(None, degrees_error(90)),
        'longitude': Setting(None, degrees_error(180)),
    }}


async def run(device):
    """Runs the schedule while it has events (base.scheduler), and only checks the clock while it has none."""
    state = {'last': None, 'caught_up': False}
    while True:
        if device.settings.get('schedule', 'events'):
            from base import scheduler
            await scheduler.run(device, state)
            del scheduler
            release('base.scheduler')
        else:
            # A start with no events is caught up already: events added later run from when they're added
            now, _ = device.platform.local_time()
            if now is not None:
                state['caught_up'] = True
                state['last'] = now if state['last'] is None else max(state['last'], now)
        await asyncio.sleep(CHECK_S)
