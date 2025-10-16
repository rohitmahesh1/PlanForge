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
- `calendar.create(title, start, end, attendees?, location?, notes?, calendar_id?, priority?)`
- `calendar.update(event_id, patch)`
- `calendar.move(event_id, new_start, new_end)`
- `calendar.delete(event_id)`
- `calendar.reorg_today(now, delay_min)`
- `tasks.add(title, due?, estimate_min?)`
- `ops.undo(op_id?)`
- `prefs.get()` / `prefs.update(...)`
- `policies.save(text, json?, active)` / `policies.list()` / `policies.delete(policy_id)`
