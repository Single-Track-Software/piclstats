# Backlog

Things agreed but not started. Decisions live in `DECISIONS.md`; this is the
queue. Newest at the bottom.

## Rally timing (ADR 005)

- **Photo evidence** (requirements F12, F14; phase 2b). A photo at the moment
  of recording, stored with the crossing and shown to officials beside a
  disputed time. Needs object storage (Tigris on Fly, see ADR 006), presigned
  uploads from the station page with the photo queued offline like the
  record itself, a retention period per PICL's release form, and battery
  measured with capture on (N5). Deferred by Chris on 2026-09-23 until the
  hosting decision.
- **Local dirt support.** Chris's idea (2026-09-23): the timing mechanism
  fits events outside PICL. Today the only league-specific parts are the
  roster category → HS/MS group mapping and the publish step into piclstats
  results; an event type with its own segment groups and a publish target
  would cover it. Details to come from Chris.
- Video backup and plate suggestion from photos (F11, F13): not planned.
- Registration export as a roster source, once PICL sends a sample file.
- Rally points: how PICL scores a rally into the season, once known.

## Site

- Descent-per-mile on the course page and in rally segment rankings, once
  elevation loss figures are entered (PR #63).
- Forecast ratings: modal lap count instead of max laps; fatigue from lap
  splits; MS→HS bridge fitted from riders who moved up (see ADR 003
  follow-ups).
- Staging: registration import and saved start orders per race, waiting on
  PICL (2026-09-20).
