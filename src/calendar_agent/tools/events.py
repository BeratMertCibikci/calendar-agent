"""Calendar tools: create, list, update, delete, find free time."""
from datetime import datetime, time, timedelta

from ..context import get_context
from ..errors import ToolError
from ..registry import tool
from ..timeutil import (describe_event, event_bounds, is_all_day, parse_date, parse_dt, parse_hhmm,
                        round_up)

MAX_LIST = 50
MAX_FREE_SLOT_DAYS = 14


def _blocking(events):
    """Timed events that actually occupy the user's time (all-day and 'free' events do not)."""
    return [e for e in events if not is_all_day(e) and e.get("transparency") != "transparent"]


@tool
def create_event(
    title: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    recurrence: str = "",
    reminder_minutes: int = 0,
    force: bool = False,
) -> str:
    """Create a calendar event. Checks for conflicts first and does NOT create the event if one exists.

    Args:
        title: Event title.
        start: Start, ISO 8601 in the user's local time (e.g. 2026-10-02T15:00:00).
        end: End, same format. If the user gave no duration, use one hour after start.
        location: Place (optional).
        description: Notes (optional).
        recurrence: Optional RRULE string. Examples: "RRULE:FREQ=WEEKLY;BYDAY=TU" (every Tuesday),
            "RRULE:FREQ=DAILY;COUNT=5", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261218T235959Z".
            The start date must be the first occurrence.
        reminder_minutes: Popup reminder this many minutes before the start. 0 keeps the calendar default.
        force: Create even if there is a conflict. Only set True after the user has seen the conflict and said yes.
    """
    ctx = get_context()
    tz = ctx.config.tz
    title = title.strip()
    if not title:
        raise ToolError("The title is empty.")
    start_dt, end_dt = parse_dt(start, tz), parse_dt(end, tz)
    if end_dt <= start_dt:
        raise ToolError("The end must be after the start.")
    if recurrence and not recurrence.upper().startswith("RRULE:"):
        raise ToolError("recurrence must start with 'RRULE:' (e.g. RRULE:FREQ=WEEKLY;BYDAY=TU).")
    if not 0 <= reminder_minutes <= 40320:
        raise ToolError("reminder_minutes must be between 0 and 40320 (four weeks).")

    # Only the first occurrence is checked for recurring events.
    if not force and not recurrence:
        busy = _blocking(ctx.calendar.list_events(start_dt.isoformat(), end_dt.isoformat()))
        if busy:
            lines = "\n".join(f"- {describe_event(e, tz)}" for e in busy)
            return ("CONFLICT: the event was NOT created. It overlaps with:\n" + lines +
                    "\nAsk the user what to do. Only if they confirm, call create_event again with force=True.")

    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": ctx.config.timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": ctx.config.timezone},
    }
    if recurrence:
        body["recurrence"] = [recurrence]
    if reminder_minutes > 0:
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
    created = ctx.calendar.insert_event(body)
    return f"Created: {describe_event(created, tz)}" + (f" | {created['htmlLink']}" if created.get("htmlLink") else "")


@tool
def list_events(start: str, end: str, query: str = "") -> str:
    """List events in a time range. Also use this to find the id of an event before updating or deleting it.

    Args:
        start: Range start, ISO 8601 (e.g. 2026-10-02T00:00:00).
        end: Range end, ISO 8601.
        query: Optional word to look for in title, description or location (e.g. "dentist").
    """
    ctx = get_context()
    tz = ctx.config.tz
    events = ctx.calendar.list_events(parse_dt(start, tz).isoformat(), parse_dt(end, tz).isoformat(), query=query)
    if not events:
        return "No events in this range."
    lines = [f"- {describe_event(e, tz)}" for e in events[:MAX_LIST]]
    if len(events) > MAX_LIST:
        lines.append(f"... and {len(events) - MAX_LIST} more. Narrow the range to see them.")
    return "\n".join(lines)


@tool
def update_event(
    event_id: str,
    title: str = "",
    start: str = "",
    end: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Change an existing event. Only the fields you pass are changed. Find the id with list_events first.

    Args:
        event_id: The id from list_events output (the part after id=).
        title: New title (optional).
        start: New start, ISO 8601 (optional). If end is not given, the original duration is kept.
        end: New end, ISO 8601 (optional).
        location: New location (optional).
        description: New description (optional).
    """
    ctx = get_context()
    tz = ctx.config.tz
    old = ctx.calendar.get_event(event_id)
    if is_all_day(old):
        raise ToolError("This is an all-day event; this tool only edits timed events.")

    patch: dict = {}
    if title.strip():
        patch["summary"] = title.strip()
    if location:
        patch["location"] = location
    if description:
        patch["description"] = description
    if start or end:
        old_start, old_end = event_bounds(old, tz)
        new_start = parse_dt(start, tz) if start else old_start
        new_end = parse_dt(end, tz) if end else new_start + (old_end - old_start)  # keep duration
        if new_end <= new_start:
            raise ToolError("The end must be after the start.")
        patch["start"] = {"dateTime": new_start.isoformat(), "timeZone": ctx.config.timezone}
        patch["end"] = {"dateTime": new_end.isoformat(), "timeZone": ctx.config.timezone}
    if not patch:
        raise ToolError("Nothing to change: pass at least one of title, start, end, location, description.")

    new = ctx.calendar.patch_event(event_id, patch)
    return f"Updated.\nBefore: {describe_event(old, tz)}\nAfter:  {describe_event(new, tz)}"


def _preview_delete(event_id: str) -> str:
    ctx = get_context()
    event = ctx.calendar.get_event(event_id)  # raises EventNotFound for a bad id, before the user is asked
    return f"Delete event: {describe_event(event, ctx.config.tz)}"


@tool(requires_approval=True, preview=_preview_delete)
def delete_event(event_id: str) -> str:
    """Delete an event. The user must approve it with a button in the app, so after calling this,
    say that the deletion is waiting for their approval. Find the id with list_events first.

    Args:
        event_id: The id from list_events output (the part after id=).
    """
    ctx = get_context()
    event = ctx.calendar.get_event(event_id)
    ctx.calendar.delete_event(event_id)
    return f"Deleted: {event.get('summary') or '(untitled)'}"


@tool
def find_free_slots(
    start_date: str,
    end_date: str,
    min_minutes: int = 60,
    day_start: str = "09:00",
    day_end: str = "19:00",
) -> str:
    """Find free time between busy events. Use this for questions like "when am I free?" instead of
    reasoning over a raw event list. All-day events do not block time.

    Args:
        start_date: First day, YYYY-MM-DD.
        end_date: Last day (inclusive), YYYY-MM-DD. At most 14 days.
        min_minutes: Only report gaps at least this long (5 to 720).
        day_start: Start of the daily window, HH:MM.
        day_end: End of the daily window, HH:MM.
    """
    ctx = get_context()
    tz = ctx.config.tz
    first, last = parse_date(start_date), parse_date(end_date)
    if last < first:
        raise ToolError("end_date must not be before start_date.")
    if (last - first).days + 1 > MAX_FREE_SLOT_DAYS:
        raise ToolError(f"Ask for at most {MAX_FREE_SLOT_DAYS} days at a time.")
    if not 5 <= min_minutes <= 720:
        raise ToolError("min_minutes must be between 5 and 720.")
    window_start, window_end = parse_hhmm(day_start), parse_hhmm(day_end)
    if window_end <= window_start:
        raise ToolError("day_end must be after day_start.")

    range_start = datetime.combine(first, time.min, tzinfo=tz)
    range_end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz)
    busy = sorted(event_bounds(e, tz) for e in
                  _blocking(ctx.calendar.list_events(range_start.isoformat(), range_end.isoformat())))
    now = round_up(ctx.now())
    needed = timedelta(minutes=min_minutes)

    lines = []
    day = first
    while day <= last:
        lo = datetime.combine(day, window_start, tzinfo=tz)
        hi = datetime.combine(day, window_end, tzinfo=tz)
        day += timedelta(days=1)
        if hi <= now:
            continue  # the window is already over
        cursor, free = max(lo, now), []
        for b_start, b_end in busy:
            if b_end <= cursor:
                continue
            if b_start >= hi:
                break
            if b_start > cursor:
                free.append((cursor, b_start))
            cursor = max(cursor, b_end)
            if cursor >= hi:
                break
        if cursor < hi:
            free.append((cursor, hi))
        free = [(a, b) for a, b in free if b - a >= needed]
        label = f"{lo:%Y-%m-%d (%a)}"
        if free:
            parts = ", ".join(f"{a:%H:%M}-{b:%H:%M} ({int((b - a).total_seconds() // 60)} min)" for a, b in free)
            lines.append(f"{label}: {parts}")
        else:
            lines.append(f"{label}: no free slot of {min_minutes}+ minutes")
    return "\n".join(lines) or "No remaining time in that window."
