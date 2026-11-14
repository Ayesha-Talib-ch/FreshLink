"""Calendar effects for Pakistan, used as model features (plan 7.1: "Ramadan / Eid effects").

Islamic dates follow the moon, so these are the expected dates; Pakistan's announcement can differ
by a day. That small difference doesn't matter for a sales pattern, but update the table each year."""

from datetime import date, timedelta

# (first day of Ramadan, Eid al-Fitr, Eid al-Adha)
_ISLAMIC = {
    2024: (date(2024, 3, 12), date(2024, 4, 10), date(2024, 6, 17)),
    2025: (date(2025, 3, 2), date(2025, 3, 31), date(2025, 6, 7)),
    2026: (date(2026, 2, 19), date(2026, 3, 21), date(2026, 5, 27)),
    2027: (date(2027, 2, 9), date(2027, 3, 10), date(2027, 5, 17)),
    2028: (date(2028, 1, 29), date(2028, 2, 27), date(2028, 5, 5)),
}
# Fixed national holidays: Kashmir Day, Pakistan Day, Labour Day, Youm-e-Takbeer,
# Independence Day, Iqbal Day, Quaid-e-Azam Day
_NATIONAL = {(2, 5), (3, 23), (5, 1), (5, 28), (8, 14), (11, 9), (12, 25)}
EID_DAYS = 3  # Eid holidays last about three days


def is_ramadan(day: date) -> bool:
    dates = _ISLAMIC.get(day.year)
    return bool(dates) and dates[0] <= day < dates[1]


def is_eid(day: date) -> bool:
    for year in (day.year, day.year - 1):
        dates = _ISLAMIC.get(year)
        if dates and any(start <= day < start + timedelta(days=EID_DAYS) for start in dates[1:]):
            return True
    return False


def is_holiday(day: date) -> bool:
    return (day.month, day.day) in _NATIONAL or is_eid(day)


def days_until_eid(day: date, horizon: int = 14) -> int:
    """Days until the next Eid (capped): shops stock up before Eid. horizon = 'not soon'."""
    for ahead in range(horizon):
        if is_eid(day + timedelta(days=ahead)):
            return ahead
    return horizon


def covered(day: date) -> bool:
    """True when the table knows this year (the API warns when it doesn't)."""
    return day.year in _ISLAMIC
