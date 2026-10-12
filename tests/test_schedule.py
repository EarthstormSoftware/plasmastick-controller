import calendar
import datetime

import pytest

import clock
import schedule
from ledstrip import MODES


def utc(*args):
    return calendar.timegm(datetime.datetime(*args).timetuple())


@pytest.mark.parametrize('days', [0, 1, 59, 365, 10957, 20738, -1, -700000, 50000])
def test_civil_dates_round_trip_and_match_python(days):
    y, m, d = clock.civil_from_days(days)
    assert clock.days_from_civil(y, m, d) == days
    assert datetime.date(1970, 1, 1) + datetime.timedelta(days=days) == datetime.date(y, m, d)
    assert clock.weekday(days) == datetime.date(y, m, d).weekday()


@pytest.mark.parametrize('when, expected', [
    (utc(2026, 3, 29, 0, 59), False), (utc(2026, 3, 29, 1, 0), True),  # starts 01:00 UTC, last Sunday of March
    (utc(2026, 7, 1), True),
    (utc(2026, 10, 25, 0, 59), True), (utc(2026, 10, 25, 1, 0), False),  # ends last Sunday of October
    (utc(2026, 12, 1), False)])
def test_eu_daylight_saving(when, expected):
    assert clock.dst_active(when, 60, 'eu') == expected


@pytest.mark.parametrize('when, expected', [
    # New York (UTC-5): 2026-03-08 02:00 EST = 07:00 UTC; 2026-11-01 02:00 EDT = 06:00 UTC.
    (utc(2026, 3, 8, 6, 59), False), (utc(2026, 3, 8, 7, 0), True),
    (utc(2026, 11, 1, 5, 59), True), (utc(2026, 11, 1, 6, 0), False)])
def test_us_daylight_saving(when, expected):
    assert clock.dst_active(when, -300, 'us') == expected


def test_offsets_and_local_iso_time():
    summer = utc(2026, 10, 12, 15, 21, 48)
    assert clock.offset_s(summer, 60, 'eu') == 7200
    assert clock.offset_s(summer, 60, 'none') == 3600
    assert clock.iso(summer, 7200) == '2026-10-12T17:21:48+02:00'
    assert clock.iso(summer, -18000) == '2026-10-12T10:21:48-05:00'
    assert clock.iso(summer, 19800) == '2026-10-12T20:51:48+05:30'  # India


def test_sunrise_and_sunset():
    # London, 2026-06-21: about 03:43 and 20:21 UTC.
    day = clock.days_from_civil(2026, 6, 21)
    rise, sset = schedule.sun_times(day, 51.5074, -0.1278)
    assert abs(rise - utc(2026, 6, 21, 3, 43)) < 180
    assert abs(sset - utc(2026, 6, 21, 20, 21)) < 180
    # Tromsø in midwinter: no sunrise at all.
    assert schedule.sun_times(clock.days_from_civil(2026, 12, 21), 69.65, 18.96) == (None, None)


EVERY_DAY = list(range(7))


def ev(at, action='on', days=EVERY_DAY, **kw):
    e = {'days': days, 'at': at, 'action': action}
    e.update(kw)
    return e


def test_events_are_checked():
    good = [ev('18:30', mode='Fire', brightness=60), ev('sunset', offset=-30), ev('23:00', 'off', days=[5, 6])]
    assert schedule.schedule_error(good, MODES) is None
    for bad, why in [
            (ev('24:00'), 'at must be'), (ev('noon'), 'at must be'), (ev('18:30', days=[]), 'days'),
            (ev('18:30', days=[7]), 'days'), (ev('18:30', 'dim'), 'action'), (ev('18:30', mode='Disco'), 'mode'),
            (ev('18:30', brightness=0), 'brightness'), (ev('sunset', offset=500), 'offset')]:
        assert why in schedule.schedule_error([bad], MODES)
    assert 'at most' in schedule.schedule_error([ev('01:00')] * 9, MODES)
    assert schedule.schedule_error('x', MODES) == 'schedule must be a list'


def local(*args):
    return utc(*args)  # local seconds: just the wall-clock time, as if UTC


def test_events_fall_due_in_order_and_only_on_their_days():
    events = [ev('23:00', 'off'), ev('18:30'), ev('07:00', days=[0])]  # 07:00 Mondays only
    sunday = local(2026, 10, 11, 0, 0)
    assert schedule.due(events, sunday + 18 * 3600, sunday + 23 * 3600, 0, None, None) == [events[1], events[0]]
    assert schedule.due(events, sunday + 23 * 3600, sunday + 23 * 3600 + 30, 0, None, None) == []  # already done
    monday = sunday + 86400
    assert schedule.due(events, monday + 6 * 3600, monday + 8 * 3600, 0, None, None) == [events[2]]
    assert schedule.due(events, sunday + 6 * 3600, sunday + 8 * 3600, 0, None, None) == []


def test_sun_events_need_a_location_and_follow_the_sun():
    events = [ev('sunset', offset=30)]
    day = local(2026, 6, 21, 0, 0)
    assert schedule.due(events, day, day + 86399, 3600, None, None) == []
    # London in June, local = UTC+1: sunset about 21:21, so +30 min is about 21:51.
    minute = schedule.event_minute(events[0], day // 86400, 3600, 51.5074, -0.1278)
    assert abs(minute - (21 * 60 + 51)) <= 3


def test_after_a_restart_the_latest_event_says_what_to_show():
    events = [ev('18:30', mode='Fire'), ev('23:00', 'off')]
    day = local(2026, 10, 12, 0, 0)
    assert schedule.latest(events, day + 20 * 3600, 0, None, None) == events[0]  # 20:00: on
    assert schedule.latest(events, day + 2 * 3600, 0, None, None) == events[1]  # 02:00: still off from last night
    assert schedule.latest([], day, 0, None, None) is None
