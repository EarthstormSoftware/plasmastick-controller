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
right). Settings: schedule.events, schedule.latitude and schedule.longitude.
"""
from math import sin, cos, asin, acos, radians, degrees

from base.compat import asyncio
from base.log import Log
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


def weekday(days):
    """0 (Monday) to 6 for the date days after 1970-01-01 (a Thursday)."""
    return (days + 3) % 7


def sun_times(days, lat, lon):
    """(sunrise, sunset) on the UTC date days (from 1970) at lat, lon, as seconds since 1970; None for either if the
    sun doesn't rise or set that day."""
    n = days - 10957 - lon / 360  # days from 2000-01-01, at local solar noon
    m = (357.5291 + 0.98560028 * n) % 360
    c = 1.9148 * sin(radians(m)) + 0.02 * sin(radians(2 * m)) + 0.0003 * sin(radians(3 * m))
    ecliptic = (m + c + 180 + 102.9372) % 360
    transit = 2451545.0 + n + 0.0053 * sin(radians(m)) - 0.0069 * sin(radians(2 * ecliptic))
    declination = asin(sin(radians(ecliptic)) * sin(radians(23.4397)))
    cos_hour = (sin(radians(-0.833)) - sin(radians(lat)) * sin(declination)) / (cos(radians(lat)) * cos(declination))
    if not -1 <= cos_hour <= 1:
        return None, None  # polar day or night
    hour = degrees(acos(cos_hour)) / 360

    def unix(jd):
        return int((jd - 2440587.5) * 86400 + 0.5)
    return unix(transit - hour), unix(transit + hour)


def event_minute(ev, local_days, offset, lat, lon):
    """When ev happens on the local date local_days, as minutes after local midnight; None if it doesn't happen that
    day (or there's no sun time). offset: local time minus UTC, in seconds."""
    if weekday(local_days) not in ev['days']:
        return None
    at = ev['at']
    if at not in SUN:
        return int(at[:2]) * 60 + int(at[3:])
    if lat is None or lon is None:
        return None
    sun = sun_times(local_days, lat, lon)[at == 'sunset']
    if sun is None:
        return None
    minute = (sun + offset - local_days * 86400) // 60 + ev.get('offset', 0)
    return minute if 0 <= minute < 1440 else None


def due(events, since, now, offset, lat, lon):
    """The events after local time since and up to now (local seconds since 1970), in time order. A day at most."""
    found = []
    for day in range(since // 86400, now // 86400 + 1):
        for ev in events:
            minute = event_minute(ev, day, offset, lat, lon)
            if minute is not None and since < day * 86400 + minute * 60 <= now:
                found.append((day * 86400 + minute * 60, ev))
    found.sort(key=lambda f: f[0])
    return [ev for _, ev in found]


def latest(events, now, offset, lat, lon):
    """The last event before local time now within the past day, or None: what the light should be doing now."""
    past = due(events, now - 86400, now, offset, lat, lon)
    return past[-1] if past else None


def run_event(device, ev):
    Log.info(TAG, '{} ({})'.format(ev['action'], ev['at']))
    light = device.light
    if light is None:
        device.product.scheduled(device, ev['action'] == 'on')
    elif ev['action'] == 'off':
        light.set(on=False)
    else:
        light.set(on=True, mode=ev.get('mode'), brightness=ev.get('brightness'))


async def run(device):
    """Does the events as they come, once the clock is right; first catching up with the latest."""
    settings, platform = device.settings, device.platform
    last, caught_up = None, False
    while True:
        now, offset = platform.local_time()
        if now is not None:
            # When the clocks go forward, the events in the hour skipped run; going back, those in the hour
            # repeated run once
            if last is None:
                last = now
            events = settings.get('schedule', 'events')
            lat, lon = settings.get('schedule', 'latitude'), settings.get('schedule', 'longitude')
            try:
                if not caught_up:
                    ev = latest(events, now, offset, lat, lon)
                    if ev:
                        Log.info(TAG, 'Catching up after the start')
                        run_event(device, ev)
                    caught_up = True
                else:
                    for ev in due(events, last, now, offset, lat, lon):
                        run_event(device, ev)
            except (KeyError, TypeError, ValueError) as e:  # an event edited by hand
                Log.warn(TAG, 'Skipping an unusable event ({!r})'.format(e))
            last = max(last, now)
        await asyncio.sleep(CHECK_S)
