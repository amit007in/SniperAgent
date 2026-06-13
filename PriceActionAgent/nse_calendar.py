"""
NSE Trading Calendar
=====================
Guards: is a given date a valid NSE trading day?
Used by daily_update.py to avoid running on weekends / holidays.
"""
import datetime
from config import NSE_HOLIDAYS


def is_trading_day(date: datetime.date) -> bool:
    """Return True if `date` is an NSE trading day."""
    if date.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    iso = date.isoformat()           # "YYYY-MM-DD"
    return iso not in NSE_HOLIDAYS


def last_trading_day(ref: datetime.date | None = None) -> datetime.date:
    """
    Return the most recent trading day on or before `ref`.
    If ref is None, uses today (IST-aware: after 16:00 IST = today,
    else previous day).
    """
    if ref is None:
        now_ist = _now_ist()
        # If market hasn't closed yet, treat yesterday as last trading day
        if now_ist.hour < 16:
            ref = (now_ist - datetime.timedelta(days=1)).date()
        else:
            ref = now_ist.date()
    d = ref
    while not is_trading_day(d):
        d -= datetime.timedelta(days=1)
    return d


def next_trading_day(ref: datetime.date | None = None) -> datetime.date:
    """Return the next NSE trading day strictly after `ref`."""
    if ref is None:
        ref = _now_ist().date()
    d = ref + datetime.timedelta(days=1)
    while not is_trading_day(d):
        d += datetime.timedelta(days=1)
    return d


def trading_days_between(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Return all trading days in [start, end] inclusive."""
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


def _now_ist() -> datetime.datetime:
    """Current datetime in IST (UTC+5:30)."""
    utc = datetime.datetime.utcnow()
    return utc + datetime.timedelta(hours=5, minutes=30)
