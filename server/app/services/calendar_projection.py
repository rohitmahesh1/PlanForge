from __future__ import annotations

from typing import Any, Dict


def summarize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    start_obj = event.get("start") or {}
    end_obj = event.get("end") or {}
    attendees = [
        attendee.get("email")
        for attendee in event.get("attendees", [])
        if attendee.get("email")
    ]
    priority = ((event.get("extendedProperties") or {}).get("private") or {}).get(
        "priority"
    )
    return {
        "id": event.get("id") or "",
        "title": event.get("summary") or "",
        "start": start_obj.get("dateTime") or start_obj.get("date"),
        "end": end_obj.get("dateTime") or end_obj.get("date"),
        "all_day": "date" in start_obj or "date" in end_obj,
        "location": event.get("location"),
        "attendees": attendees,
        "priority": priority,
        "status": event.get("status"),
    }


def detail_event(event: Dict[str, Any]) -> Dict[str, Any]:
    details = summarize_event(event)
    details["notes"] = event.get("description")
    details["calendar_id"] = _calendar_id_from_event(event)
    details["html_link"] = event.get("htmlLink")
    return details


def _calendar_id_from_event(event: Dict[str, Any]) -> str | None:
    organizer = (event.get("organizer") or {}).get("email")
    if organizer:
        return organizer
    creator = (event.get("creator") or {}).get("email")
    if creator:
        return creator
    return None
