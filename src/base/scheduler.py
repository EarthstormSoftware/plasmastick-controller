"""
Running the schedule (base.schedule): the sun's times, which events are due, and doing them. Loaded only while there
are events, as on a Pico every module kept costs heap.
"""
from math import sin, cos, asin, acos, radians, degrees

from base.compat import asyncio
from base.log import Log
from base.schedule import TAG, SUN, CHECK_S


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


async def run(device, state):
    """Does the events as they come, once the clock is right; first catching up with the latest. Returns when there
    are no events (base.schedule then lets this module go). state: {"last", "caught_up"}, kept between runs, so
    events added later don't count as a start."""
    settings, platform = device.settings, device.platform
    while True:
        events = settings.get('schedule', 'events')
        if not events:
            return
        now, offset = platform.local_time()
        if now is not None:
            # When the clocks go forward, the events in the hour skipped run; going back, those in the hour
            # repeated run once
            if state['last'] is None:
                state['last'] = now
            lat, lon = settings.get('schedule', 'latitude'), settings.get('schedule', 'longitude')
            try:
                if not state['caught_up']:
                    ev = latest(events, now, offset, lat, lon)
                    if ev:
                        Log.info(TAG, 'Catching up after the start')
                        run_event(device, ev)
                    state['caught_up'] = True
                else:
                    for ev in due(events, state['last'], now, offset, lat, lon):
                        run_event(device, ev)
            except (KeyError, TypeError, ValueError) as e:  # an event edited by hand
                Log.warn(TAG, 'Skipping an unusable event ({!r})'.format(e))
            state['last'] = max(state['last'], now)
        await asyncio.sleep(CHECK_S)
