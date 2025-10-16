# server/app/services/reorg.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date, timezone
from typing import List, Optional, Dict, Any, Tuple

from app.models.prefs import Prefs
from app.services.gcal import GCalClient
from app.services.timezone import (
    to_tz,
    user_now,
    day_bounds,
    parse_hm,
    SleepWindow,
    next_wake_after,
    apply_sleep_window,
)
from app.services.undo import ChangeLogger
from app.utils import from_rfc3339


@dataclass
class ReorgPlan:
    moved_ids: List[str]
    trimmed_ids: List[str]
    pushed_ids: List[str]
    op_ids: List[str]


@dataclass(frozen=True)
class _Window:
    start: datetime
    end: datetime
    event_id: Optional[str] = None


class ReorgService:
    """
    “Slept in” reorg:
      - Shift ROUTINE events forward by delay_min if they start before now+delay
      - Preserve meetings with attendees and 'high' priority
      - Enforce sleep window and simple buffers by reserving windows while we place items
      - If no room, trim down to a floor; if still no room, push to next day at wake

    Routine vs fixed heuristic:
      - fixed if event has attendees (length > 0) OR extendedProperties.private.priority == 'high'
      - routine otherwise
    """

    TRIM_FLOOR_MIN = 25  # minimal trimmed duration for a routine block

    def __init__(self, gcal: GCalClient, prefs: Prefs):
        self.gcal = gcal
        self.prefs = prefs

    async def shift_day(self, *, now: datetime, delay_min: int) -> ReorgPlan:
        tz = self.gcal.user.timezone or "UTC"
        now_local = to_tz(now, tz)
        # Today's local bounds
        today_start_local, today_end_local = day_bounds(now_local.date(), tz)

        # Fetch all events for today (expanded)
        events = await self.gcal.list_events(today_start_local, today_end_local)

        # Partition events
        fixed_events: List[dict] = []
        routine_events: List[dict] = []
        cutoff_local = now_local + timedelta(minutes=delay_min)

        for ev in events:
            # Skip all-day (date-only)
            if _is_all_day(ev):
                continue

            start_dt = _ev_dt_local(ev.get("start"), tz)
            if start_dt is None:
                continue

            priority = _get_priority(ev)
            has_attendees = bool(ev.get("attendees"))

            if has_attendees or priority == "high":
                fixed_events.append(ev)
                continue

            # Candidate routine events to move: those starting before now+delay
            if start_dt < cutoff_local:
                routine_events.append(ev)

        # Build initial busy set from fixed events + sleep
        busy = _merge_and_clip(
            _event_windows_with_buffer(fixed_events, self.prefs, tz)
            + _sleep_windows_covering(today_start_local, today_end_local, self.prefs, tz),
            today_start_local,
            today_end_local,
        )

        moved: List[str] = []
        trimmed: List[str] = []
        pushed: List[str] = []
        op_ids: List[str] = []

        # Process routine events chronologically
        routine_events.sort(key=lambda e: _ev_dt_local(e.get("start"), tz) or today_start_local)

        logger = ChangeLogger(self.gcal.user)

        for ev in routine_events:
            eid = ev.get("id")
            start_loc = _ev_dt_local(ev.get("start"), tz)
            end_loc = _ev_dt_local(ev.get("end"), tz)
            if not start_loc or not end_loc:
                continue

            original_dur = end_loc - start_loc
            desired_start = start_loc + timedelta(minutes=delay_min)
            desired_end = end_loc + timedelta(minutes=delay_min)

            # Guard against sleep: if the shifted interval falls into sleep, move it to next wake
            adj_start, adj_end = apply_sleep_window(
                desired_start, desired_end, self.prefs.sleep_start, self.prefs.sleep_end, tz
            )

            # Try to place today (after adj_start)
            placed_start, placed_end, placement_kind = _fit_into_busy(
                desired_range=(adj_start, today_end_local),
                desired_interval=(adj_start, adj_end),
                busy=busy,
                full_duration=original_dur,
                trim_floor=timedelta(minutes=self.TRIM_FLOOR_MIN),
            )

            if placed_start is None:
                # Push to next day at wake
                next_day_wake = _next_wake_tomorrow(today_end_local, self.prefs, tz)
                # Build busy for tomorrow using fixed events there + sleep
                tomorrow_end_local = next_day_wake.replace(hour=23, minute=59, second=59, microsecond=0)
                tomorrow_events = await self.gcal.list_events(next_day_wake, tomorrow_end_local)
                tomorrow_fixed = [e for e in tomorrow_events if (e.get("attendees") or _get_priority(e) == "high")]
                busy_tomorrow = _merge_and_clip(
                    _event_windows_with_buffer(tomorrow_fixed, self.prefs, tz)
                    + _sleep_windows_covering(next_day_wake, tomorrow_end_local, self.prefs, tz),
                    next_day_wake,
                    tomorrow_end_local,
                )

                placed_start, placed_end, placement_kind = _fit_into_busy(
                    desired_range=(next_day_wake, tomorrow_end_local),
                    desired_interval=(next_day_wake, next_day_wake + original_dur),
                    busy=busy_tomorrow,
                    full_duration=original_dur,
                    trim_floor=timedelta(minutes=self.TRIM_FLOOR_MIN),
                )
                if placed_start is None:
                    # As a last resort, drop to exact wake preserving at least trim floor
                    placed_start = next_day_wake
                    placed_end = next_day_wake + timedelta(minutes=self.TRIM_FLOOR_MIN)
                    placement_kind = "pushed"

                pushed.append(eid)
                # Also reserve in today's busy? No; it lands tomorrow.

                # Apply update & log
                before = ev  # fetched earlier
                patched = await self.gcal.update_event(
                    event_id=eid,
                    patch={"start": placed_start, "end": placed_end},
                )
                entry = await logger.record_update(event_id=eid, before_json=before, after_json=patched)
                op_ids.append(entry.op_id)
                continue

            # Reserve this window in today's busy so subsequent moves don't collide
            busy = _reserve(busy, (placed_start, placed_end))

            if placement_kind == "trimmed":
                trimmed.append(eid)
            else:
                moved.append(eid)

            # Apply update & log
            before = ev
            patched = await self.gcal.update_event(
                event_id=eid,
                patch={"start": placed_start, "end": placed_end},
            )
            entry = await logger.record_update(event_id=eid, before_json=before, after_json=patched)
            op_ids.append(entry.op_id)

        return ReorgPlan(moved_ids=moved, trimmed_ids=trimmed, pushed_ids=pushed, op_ids=op_ids)


# -----------------------
# Helpers (pure-ish)
# -----------------------

def _is_all_day(ev: Dict[str, Any]) -> bool:
    return "date" in (ev.get("start") or {}) or "date" in (ev.get("end") or {})


def _get_priority(ev: Dict[str, Any]) -> Optional[str]:
    return ((ev.get("extendedProperties") or {}).get("private") or {}).get("priority")


def _ev_dt_local(when: Dict[str, Any] | None, tz: str) -> Optional[datetime]:
    if not when:
        return None
    if "dateTime" in when:
        return to_tz(from_rfc3339(when["dateTime"]), tz)
    return None  # ignore all-day for reorg


def _event_windows_with_buffer(items: List[Dict[str, Any]], prefs: Prefs, tz: str) -> List[_Window]:
    out: List[_Window] = []
    buf = timedelta(minutes=max(0, int(prefs.min_buffer_min)))
    for ev in items:
        start_obj = ev.get("start") or {}
        end_obj = ev.get("end") or {}
        if "date" in start_obj or "date" in end_obj:
            continue
        if not start_obj.get("dateTime") or not end_obj.get("dateTime"):
            continue
        start = to_tz(from_rfc3339(start_obj["dateTime"]), tz) - buf
        end = to_tz(from_rfc3339(end_obj["dateTime"]), tz) + buf
        # Normalize tz to tz-aware; convert back to start/end tz if needed
        out.append(_Window(start=start, end=end, event_id=ev.get("id")))
    return out


def _sleep_windows_covering(start: datetime, end: datetime, prefs: Prefs, tz: str) -> List[_Window]:
    sw = SleepWindow(parse_hm(prefs.sleep_start), parse_hm(prefs.sleep_end))
    windows: List[_Window] = []

    cur_date = (start - timedelta(days=1)).date()
    last_date = (end + timedelta(days=1)).date()

    while cur_date <= last_date:
        start_local = datetime.combine(cur_date, sw.start_hm, tzinfo=to_tz(start, tz).tzinfo)
        end_local = datetime.combine(cur_date, sw.end_hm, tzinfo=to_tz(end, tz).tzinfo)
        if sw.start_hm >= sw.end_hm:
            end_local = end_local + timedelta(days=1)
        windows.append(_Window(start=start_local, end=end_local, event_id=None))
        cur_date = cur_date + timedelta(days=1)
    return windows


def _merge_and_clip(busy: List[_Window], start: datetime, end: datetime) -> List[_Window]:
    if not busy:
        return []
    busy_sorted = sorted(busy, key=lambda w: w.start)
    merged: List[_Window] = []
    cur = busy_sorted[0]
    for nxt in busy_sorted[1:]:
        if nxt.start <= cur.end:
            cur = _Window(start=cur.start, end=max(cur.end, nxt.end), event_id=None)
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)

    clipped: List[_Window] = []
    for w in merged:
        s = max(w.start, start)
        e = min(w.end, end)
        if e > s:
            clipped.append(_Window(start=s, end=e, event_id=w.event_id))
    return clipped


def _invert_to_free(busy: List[_Window], start: datetime, end: datetime) -> List[_Window]:
    if not busy:
        return [_Window(start=start, end=end)]
    free: List[_Window] = []
    cur = start
    for b in busy:
        if b.start > cur:
            free.append(_Window(start=cur, end=b.start))
        cur = max(cur, b.end)
    if cur < end:
        free.append(_Window(start=cur, end=end))
    return free


def _first_fit_after(
    free: List[_Window],
    earliest_start: datetime,
    duration: timedelta,
) -> Optional[Tuple[datetime, datetime]]:
    for w in free:
        start = max(w.start, earliest_start)
        if w.end - start >= duration:
            return start, start + duration
    return None


def _fit_into_busy(
    *,
    desired_range: Tuple[datetime, datetime],
    desired_interval: Tuple[datetime, datetime],
    busy: List[_Window],
    full_duration: timedelta,
    trim_floor: timedelta,
) -> Tuple[Optional[datetime], Optional[datetime], Optional[str]]:
    """
    Try to place desired_interval within desired_range avoiding busy windows.
    Returns (start, end, kind) where kind is "moved" | "trimmed" | "pushed" | None
    """
    start_bound, end_bound = desired_range
    desired_start, desired_end = desired_interval

    # Merge & invert to compute free windows in [start_bound, end_bound)
    busy_in_range = _merge_and_clip([w for w in busy if w.end > start_bound and w.start < end_bound], start_bound, end_bound)
    free = _invert_to_free(busy_in_range, start_bound, end_bound)

    # Try full duration at/after desired_start
    res = _first_fit_after(free, earliest_start=desired_start, duration=full_duration)
    if res:
        s, e = res
        return s, e, "moved"

    # Try trimmed duration (down to floor)
    trimmed_dur = max(trim_floor, timedelta(minutes=1))
    res = _first_fit_after(free, earliest_start=desired_start, duration=trimmed_dur)
    if res:
        s, e = res
        return s, e, "trimmed"

    # No fit
    return None, None, None


def _reserve(busy: List[_Window], interval: Tuple[datetime, datetime]) -> List[_Window]:
    """Reserve (start, end) by adding to busy and merging."""
    start, end = interval
    return _merge_and_clip(busy + [_Window(start=start, end=end)], min(start, busy[0].start if busy else start), max(end, busy[-1].end if busy else end))


def _next_wake_tomorrow(today_end_local: datetime, prefs: Prefs, tz: str) -> datetime:
    """Return tomorrow's wake time in local tz."""
    sw = SleepWindow(parse_hm(prefs.sleep_start), parse_hm(prefs.sleep_end))
    # Find wake after the end of today (23:59:59) → will be tomorrow's wake
    just_after_today = today_end_local + timedelta(minutes=1)
    return next_wake_after(just_after_today, sw)
