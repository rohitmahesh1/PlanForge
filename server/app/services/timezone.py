# server/app/services/timezone.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Tuple

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception as _exc:  # pragma: no cover
    ZoneInfo = None  # type: ignore


# -----------------------------
# Basic TZ helpers
# -----------------------------

def _tz(tz_str: str):
    """Return a ZoneInfo timezone. Raise ValueError if unavailable."""
    if not ZoneInfo:
        raise ValueError("zoneinfo not available in this Python environment")
    try:
        return ZoneInfo(tz_str)
    except Exception as exc:
        raise ValueError(f"Invalid IANA timezone: {tz_str}") from exc


def ensure_aware(dt: datetime) -> datetime:
    """
    Ensure a datetime is timezone-aware.
    If naive, assume UTC (consistent with backend default behavior).
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_tz(dt: datetime, tz_str: str) -> datetime:
    """Convert a datetime to the given IANA timezone, preserving the instant."""
    return ensure_aware(dt).astimezone(_tz(tz_str))


def user_now(tz_str: str) -> datetime:
    """Current time in the user's timezone."""
    return datetime.now(_tz(tz_str))


# -----------------------------
# Day bounds
# -----------------------------

def day_bounds(d: date, tz_str: str) -> Tuple[datetime, datetime]:
    """
    Return [start, end) for a local day in tz_str.
    end is the exclusive bound (start + 1 day).
    """
    tz = _tz(tz_str)
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def today_bounds(tz_str: str) -> Tuple[datetime, datetime]:
    """Convenience wrapper for the local day containing 'now'."""
    now = user_now(tz_str)
    return day_bounds(now.date(), tz_str)


# -----------------------------
# Sleep window utilities
# -----------------------------

def parse_hm(hhmm: str) -> time:
    """
    Parse 'HH:MM' (24h) into datetime.time.
    """
    hh, mm = hhmm.split(":")
    return time(hour=int(hh), minute=int(mm))


@dataclass(frozen=True)
class SleepWindow:
    """Represents a daily sleep window, which may cross midnight."""
    start_hm: time  # e.g., 22:30
    end_hm: time    # e.g., 07:00 (next morning)

    @property
    def crosses_midnight(self) -> bool:
        return self.start_hm >= self.end_hm


def is_within_sleep(local_dt: datetime, sw: SleepWindow) -> bool:
    """
    Determine if a *local* datetime falls within the sleep window.
    Supports windows crossing midnight (e.g., 22:30–07:00).
    """
    t = local_dt.timetz()
    if not sw.crosses_midnight:
        return sw.start_hm <= t <= sw.end_hm
    # Crossing midnight: (t >= start) OR (t <= end)
    return (t >= sw.start_hm) or (t <= sw.end_hm)


def next_wake_after(local_dt: datetime, sw: SleepWindow) -> datetime:
    """
    Given a local datetime, return the next occurrence of sleep end_hm
    (the morning 'wake' time) at or after local_dt.
    """
    base = local_dt.replace(hour=sw.end_hm.hour, minute=sw.end_hm.minute, second=0, microsecond=0)
    if sw.crosses_midnight:
        # If it's already past today's wake, move to tomorrow's wake
        if local_dt.timetz() > sw.end_hm:
            base = base + timedelta(days=1)
        return base
    else:
        # Non-crossing: if we've already passed today's wake, the next is tomorrow
        if local_dt.timetz() > sw.end_hm:
            base = base + timedelta(days=1)
        return base


def previous_sleep_start_before(local_dt: datetime, sw: SleepWindow) -> datetime:
    """
    Return the last occurrence of sleep start_hm at or before local_dt (local time).
    Useful for truncating an interval that overlaps tonight's sleep.
    """
    start = local_dt.replace(hour=sw.start_hm.hour, minute=sw.start_hm.minute, second=0, microsecond=0)
    if local_dt.timetz() < sw.start_hm:
        start = start - timedelta(days=1)
    return start


def apply_sleep_window(
    start: datetime,
    end: datetime,
    sleep_start_hm: str,
    sleep_end_hm: str,
    tz_str: str,
) -> Tuple[datetime, datetime]:
    """
    Adjust a candidate interval [start, end) so it does NOT lie inside the user's sleep window.
    Strategy:
      - If entire interval is within sleep → move to next wake, preserving duration.
      - If interval overlaps sleep start → clamp end to sleep_start.
      - If interval starts during sleep → shift start to next wake.
      - Else → unchanged.
    Returns adjusted datetimes in the *original* timezone of the inputs.
    """
    if end <= start:
        return start, end  # degenerate; leave unchanged

    # Keep original tz for the return; compute in user's local time
    orig_tzinfo = ensure_aware(start).tzinfo
    tz = _tz(tz_str)

    start_loc = ensure_aware(start).astimezone(tz)
    end_loc = ensure_aware(end).astimezone(tz)

    sw = SleepWindow(parse_hm(sleep_start_hm), parse_hm(sleep_end_hm))
    duration = end_loc - start_loc

    start_in_sleep = is_within_sleep(start_loc, sw)
    end_in_sleep = is_within_sleep(end_loc - timedelta(microseconds=1), sw)  # treat end exclusive

    if start_in_sleep and end_in_sleep:
        # Entirely within sleep: move to next wake
        new_start_loc = next_wake_after(start_loc, sw)
        new_end_loc = new_start_loc + duration
    elif start_in_sleep and not end_in_sleep:
        # Starts in sleep, ends after wake: begin at the next wake
        new_start_loc = next_wake_after(start_loc, sw)
        # Keep original end if it is still after new start; otherwise preserve duration
        if end_loc > new_start_loc:
            new_end_loc = end_loc
        else:
            new_end_loc = new_start_loc + duration
    elif not start_in_sleep and end_in_sleep:
        # Starts before sleep, runs into sleep: clamp to sleep start
        sleep_start_loc = previous_sleep_start_before(end_loc, sw) if sw.crosses_midnight else end_loc.replace(
            hour=sw.start_hm.hour, minute=sw.start_hm.minute, second=0, microsecond=0
        )
        new_start_loc = start_loc
        new_end_loc = min(end_loc, sleep_start_loc)
        if new_end_loc <= new_start_loc:
            # If clamping eliminates the interval, move to next wake preserving duration
            new_start_loc = next_wake_after(end_loc, sw)
            new_end_loc = new_start_loc + duration
    else:
        # No overlap with sleep → unchanged
        new_start_loc, new_end_loc = start_loc, end_loc

    # Convert back to the original tz
    new_start = new_start_loc.astimezone(orig_tzinfo)
    new_end = new_end_loc.astimezone(orig_tzinfo)
    return new_start, new_end
