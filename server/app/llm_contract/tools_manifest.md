# Tools Manifest (LLM Contract)

The assistant should **plan** then call tools with minimal back-and-forth.  
Hard constraints (sleep window, buffers, collisions) are enforced server-side.

## Rules of Thumb
- Never schedule inside sleep: **22:30–07:00** (user-editable via `/prefs`).
- Meetings with attendees are **fixed** unless the user asks to propose changes.
- Decide priority yourself: `high` vs `routine`.
- Save durable preferences with `/policies/save`, then apply them on future requests.

## Tools (HTTP endpoints)
- `calendar.freebusy(start, end)` → free/busy windows
- `calendar.list(start, end, calendar_id?, limit?)` → events in a time range
- `calendar.search(query, start?, end?, calendar_id?, limit?)` → find events by title/details
- `calendar.get(event_id, calendar_id?)` → inspect one event in detail
- `calendar.create(title, start, end, attendees?, location?, notes?, calendar_id?, priority?)`
- `calendar.update(event_id, patch)`
- `calendar.move(event_id, new_start, new_end)`
- `calendar.delete(event_id)`
- `calendar.reorg_today(now, delay_min)`
- `tasks.add(title, due?, estimate_min?)`
- `tasks.list(from_date?, to_date?)`
- `tasks.complete(task_event_id)`
- `tasks.update(task_event_id, title?, due?, estimate_min?, status?)`
- `tasks.delete(task_event_id)`
- `tasks.schedule(task_event_id, start, end?, duration_min?, title?, calendar_id?, priority?)`
- `ops.undo(op_id?)`
- `ops.history(limit?)`
- `prefs.get()` / `prefs.update(...)`
- `policies.save(text, json?, active)` / `policies.list()` / `policies.delete(policy_id)`
