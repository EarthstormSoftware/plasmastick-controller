"""Local time: the board's clock is UTC (set by ntp.py); this works out the
offset for device.utc_offset_min and its daylight-saving rule (device.dst).

Dates are whole-number days since 1970-01-01, so the sums come out the same
on the board and on a PC (whose time.mktime would use the PC's own zone).
"""

# 'eu': last Sunday of March to last Sunday of October, at 01:00 UTC.
# 'us': second Sunday of March to first Sunday of November, at 02:00 local.
DST_RULES = ('none', 'eu', 'us')


def days_from_civil(y, m, d):
    """Days from 1970-01-01 to y-m-d (proleptic Gregorian)."""
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def civil_from_days(days):
    """(y, m, d) for days from 1970-01-01."""
    days += 719468
    era = days // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return yoe + era * 400 + (m <= 2), m, d


def weekday(days):
    """0 for Monday ... 6 for Sunday (1970-01-01 was a Thursday)."""
    return (days + 3) % 7


def _last_sunday(y, m):
    last = days_from_civil(y + (m == 12), m % 12 + 1, 1) - 1
    return last - (weekday(last) + 1) % 7


def _nth_sunday(y, m, n):
    first = days_from_civil(y, m, 1)
    return first + (6 - weekday(first)) % 7 + 7 * (n - 1)


def dst_active(utc, offset_min, rule):
    """True if daylight saving is in force at utc (seconds since 1970)."""
    if rule == 'eu':
        y = civil_from_days(utc // 86400)[0]
        return _last_sunday(y, 3) * 86400 + 3600 <= utc < _last_sunday(y, 10) * 86400 + 3600
    if rule == 'us':
        y = civil_from_days((utc + offset_min * 60) // 86400)[0]
        # 02:00 local standard time to 02:00 local daylight time (01:00 standard).
        start = _nth_sunday(y, 3, 2) * 86400 + 7200 - offset_min * 60
        end = _nth_sunday(y, 11, 1) * 86400 + 3600 - offset_min * 60
        return start <= utc < end
    return False


def offset_s(utc, offset_min, rule):
    """Seconds to add to UTC for local time at utc."""
    return (offset_min + (60 if dst_active(utc, offset_min, rule) else 0)) * 60


def iso(utc, offset):
    """utc as local ISO 8601 with its offset, e.g. 2026-10-12T17:21:48+02:00."""
    local = int(utc) + offset
    y, m, d = civil_from_days(local // 86400)
    s = local % 86400
    sign, o = ('+', offset) if offset >= 0 else ('-', -offset)
    return '{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}{}{:02d}:{:02d}'.format(
        y, m, d, s // 3600, s // 60 % 60, s % 60, sign, o // 3600, o // 60 % 60)
