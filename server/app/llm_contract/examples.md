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

## Screenshot of a meeting invite
**User:** (sends screenshot)
1) Extract title/time via OCR in-model.
2) `POST /calendar.create` with parsed fields.

## Undo last change
**User:** “Undo.”
1) `POST /ops/undo {}`

Keep confirmations concise: “Booked Chem prep 7–8 PM & Fri 8–9 AM. Undo?”
