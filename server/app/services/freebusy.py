# server/app/services/freebusy.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import List, Tuple, Optional, Dict, Any

from app.models.prefs import Prefs
from app.services.gcal import GCalClient
from app.services.timezone import (
    parse_hm,
    SleepWindow,
    user_now,
    to_tz,
)
from app.utils import from_rfc3339


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    event_id: Optional[str] = None


class FreeBusyService:
    """
    Builds free/busy windows from the user's Calendar while enforcing **hard constraints**:
    - Sleep window (no scheduling inside)
    - Min buffer minutes around events

    Notes:
    - MVP ignores all-day events for blocking purposes (so tasks calendar won't block time).
      If you want all-day to block, extend _event_windows() accordingly.
    """

    def __init__(self, gcal: GCalClient, prefs: Optional[Prefs] = None):
        self.gcal = gcal
        self.prefs = prefs  # if None, we fetch on demand

    # Public API ---------------------------------------------------------------

    async def query(self, start: datetime, end: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Return (free_windows, busy_windows) as lists of dicts with tz-aware datetimes.
        """
        prefs = self.prefs or await self.gcal.get_prefs()
        tz = self.gcal.user.timezone or "UTC"

        # 1) Busy windows from events (+ buffer)
        events = await self.gcal.list_events(start, end)
        busy = self._event_windows(events, prefs, tz)

        # 2) Add sleep windows covering the range
        busy += self._sleep_windows_covering(start, end, prefs, tz)

        # 3) Merge + clip
        busy = _merge_and_clip(busy, start, end)

        # 4) Compute free windows as complement
        free = _invert_to_free(busy, start, end)

        # Convert to serializable dicts (FastAPI/Pydantic will handle datetimes)
        busy_out = [{"start": b.start, "end": b.end, "event_id": b.event_id} for b in busy]
        free_out = [{"start": f.start, "end": f.end} for f in free]
        return free_out, busy_out

    async def snapshot(self, hours_ahead: int = 36) -> Dict[str, Any]:
        """
        Compact summary for planning: now → now+hours_ahead free/busy windows,
        and a few earliest free slots.
        """
        tz = self.gcal.user.timezone or "UTC"
        now = user_now(tz)
        horizon = now + timedelta(hours=hours_ahead)
        free, busy = await self.query(now, horizon)

        # First three free slots, clipped to 60 minutes each for brevity
        first_slots: List[Dict[str, Any]] = []
        for w in free[:5]:
            dur = (w["end"] - w["start"]).total_seconds() / 60.0
            first_slots.append({"start": w["start"], "end": w["start"] + timedelta(minutes=min(60, max(0, int(dur))))})
            if len(first_slots) >= 3:
                break

        return {
            "now": now,
            "horizon": horizon,
            "free": free,
            "busy": busy,
            "first_slots": first_slots,
        }

    # Internal helpers ---------------------------------------------------------

    def _event_windows(self, items: List[Dict[str, Any]], prefs: Prefs, tz: str) -> List[Window]:
        """Build busy windows (with buffers) from Calendar events."""
        out: List[Window] = []
        buf = timedelta(minutes=max(0, int(prefs.min_buffer_min)))

        for ev in items:
            start_obj = ev.get("start") or {}
            end_obj = ev.get("end") or {}
            # Ignore all-day (start.date) to avoid blocking on Tasks calendar, holidays, etc.
            if "date" in start_obj or "date" in end_obj:
                continue

            start = from_rfc3339(start_obj.get("dateTime")) if start_obj.get("dateTime") else None
            end = from_rfc3339(end_obj.get("dateTime")) if end_obj.get("dateTime") else None
            if not start or not end:
                continue

            # Respect user timezone for buffers (convert there, then back)
            start_local = to_tz(start, tz) - buf
            end_local = to_tz(end, tz) + buf

            # Convert back to the original tz of start/end (tz-aware already)
            out.append(Window(start=start_local.astimezone(start.tzinfo), end=end_local.astimezone(end.tzinfo), event_id=ev.get("id")))

        return out

    def _sleep_windows_covering(self, start: datetime, end: datetime, prefs: Prefs, tz: str) -> List[Window]:
        """
        Build blocking windows for sleep across [start, end).
        For a crossing-midnight window 22:30–07:00, each local day contributes:
          [D 22:30, D+1 07:00)
        """
        sw = SleepWindow(parse_hm(prefs.sleep_start), parse_hm(prefs.sleep_end))
        # Step day by day in user's local time
        windows: List[Window] = []

        # Convert range bounds to local dates
        cur_local = to_tz(start, tz)
        end_local = to_tz(end, tz)

        # Start from the local date before/at start to ensure coverage
        cur_date = (cur_local - timedelta(days=1)).date()
        last_date = (end_local + timedelta(days=1)).date()

        while cur_date <= last_date:
            # Sleep start at D start_hm → sleep end at D (or D+1) end_hm
            start_local = datetime.combine(cur_date, sw.start_hm, tzinfo=cur_local.tzinfo)
            end_local_dt = datetime.combine(cur_date, sw.end_hm, tzinfo=cur_local.tzinfo)
            if sw.start_hm >= sw.end_hm:
                # Cross midnight: end next day
                end_local_dt = end_local_dt + timedelta(days=1)

            windows.append(Window(start=start_local.astimezone(start.tzinfo),
                                  end=end_local_dt.astimezone(end.tzinfo),
                                  event_id=None))
            cur_date = cur_date + timedelta(days=1)

        return windows


# Composition functions --------------------------------------------------------

def _merge_and_clip(busy: List[Window], start: datetime, end: datetime) -> List[Window]:
    """Merge overlapping busy windows and clip to [start, end)."""
    if not busy:
        return []

    # Sort by start
    busy_sorted = sorted(busy, key=lambda w: w.start)
    merged: List[Window] = []
    cur = busy_sorted[0]

    for nxt in busy_sorted[1:]:
        if nxt.start <= cur.end:
            # Overlap: extend end
            cur = Window(start=cur.start, end=max(cur.end, nxt.end), event_id=None)
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)

    # Clip to [start, end)
    clipped: List[Window] = []
    for w in merged:
        s = max(w.start, start)
        e = min(w.end, end)
        if e > s:
            clipped.append(Window(start=s, end=e, event_id=w.event_id))
    return clipped


def _invert_to_free(busy: List[Window], start: datetime, end: datetime) -> List[Window]:
    """Compute free windows as the complement of busy within [start, end)."""
    if not busy:
        return [Window(start=start, end=end, event_id=None)]

    free: List[Window] = []
    cur = start
    for b in busy:
        if b.start > cur:
            free.append(Window(start=cur, end=b.start))
        cur = max(cur, b.end)
    if cur < end:
        free.append(Window(start=cur, end=end))
    return free
