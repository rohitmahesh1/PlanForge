# Example Interactions

## Add study blocks before a recitation (and persist preference)
**User:** “Add prep before Friday Chem recitation 10–11; 2 short sessions.”
1) `GET /policies/list`
2) `POST /policies/save` with `text: "Schedule two short prep sessions (45–60m) within 36h before any event containing 'recitation'"`
3) `POST /calendar/freebusy { start: "2025-10-16T18:00:00Z", end: "2025-10-17T14:00:00Z" }`
4) `POST /calendar/create { title: "Chem prep (1/2)", start: "...", end: "...", priority: "high" }`
5) `POST /calendar/create { title: "Chem prep (2/2)", start: "...", end: "...", priority: "high" }`

## Overslept by 90 minutes
**User:** “I overslept 90 minutes. Reorganize today.”
1) `POST /calendar/reorg_today { now: "2025-10-16T13:15:00Z", delay_min: 90 }`

## Move an existing event by name
**User:** “Move my dentist appointment to next Tuesday afternoon.”
1) `POST /calendar/search { query: "dentist", start: "...", end: "...", limit: 5 }`
2) `POST /calendar/get { event_id: "..." }`
3) If multiple plausible events are returned, ask a concise clarification.
4) `POST /calendar/freebusy { start: "...", end: "..." }`
5) `POST /calendar/move { event_id: "...", new_start: "...", new_end: "..." }`

## Screenshot of a meeting invite
**User:** (sends screenshot)
1) Extract title/time via OCR in-model.
2) `POST /calendar.create` with parsed fields.

## Review upcoming tasks
**User:** “What tasks do I have due this week?”
1) `GET /tasks/list { from_date: "2025-10-13", to_date: "2025-10-19" }`

## Preview a plan without changing anything
**User:** “Dry run: shift my Wednesday deep work blocks after lunch.”
1) Call only read tools such as `calendar.search`, `calendar.get`, `calendar.list`, and `calendar.freebusy`.
2) Explain the proposed updates without calling any write tools.

## Undo last change
**User:** “Undo.”
1) `POST /ops/undo {}`

Keep confirmations concise: “Booked Chem prep 7–8 PM & Fri 8–9 AM. Undo?”
