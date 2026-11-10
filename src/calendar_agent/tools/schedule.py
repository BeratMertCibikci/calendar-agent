"""Import a timetable (.ics export, e.g. from ADE) into the calendar.

The import is a *sync*, not an append: every imported event carries its iCalendar UID in a
private extended property, so running it again updates changed lessons and skips unchanged
ones instead of creating duplicates.
"""
from datetime import datetime, time
from pathlib import Path

from ..context import get_context
from ..errors import ToolError
from ..icsparse import IcsEvent, parse_ics
from ..registry import tool
from ..timeutil import event_bounds

MAX_FILE_BYTES = 5_000_000
MAX_EVENTS = 500
SOURCE_TAG = "source=ics"


def _read_events(file_path: str):
    ctx = get_context()
    path = Path(file_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ToolError(f"File not found: {file_path}") from None
    if path.suffix.lower() != ".ics" or not path.is_file():
        raise ToolError("Only .ics files can be imported.")
    allowed = [Path(d).expanduser().resolve() for d in ctx.config.ics_allowed_dirs]
    if allowed and not any(path.is_relative_to(d) for d in allowed):
        raise ToolError("That file is outside the folders the assistant may read. "
                        "Move it to your Downloads or Documents folder.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError("The file is larger than 5 MB.")
    result = parse_ics(path.read_text(encoding="utf-8", errors="replace"), ctx.config.tz)
    if not result.events:
        raise ToolError("No importable events found in that file.")
    if len(result.events) > MAX_EVENTS:
        raise ToolError(f"The file has {len(result.events)} events; the limit is {MAX_EVENTS}.")
    return path, result


def _preview_import(file_path: str) -> str:
    path, result = _read_events(file_path)
    events = sorted(result.events, key=lambda e: _sort_key(e))
    first, last = events[0].start, events[-1].start
    sample = "; ".join(f"{e.summary} ({e.start:%d/%m %H:%M})" if not e.all_day else f"{e.summary} ({e.start:%d/%m})"
                       for e in events[:3])
    skipped = f", {result.skipped} unreadable entries skipped" if result.skipped else ""
    return (f"Import {len(events)} events from {path.name} ({first:%Y-%m-%d} to {last:%Y-%m-%d}){skipped}. "
            f"First ones: {sample}")


def _sort_key(event: IcsEvent):
    start = event.start
    return start.isoformat() if hasattr(start, "isoformat") else str(start)


def _body(event: IcsEvent, tz_name: str) -> dict:
    if event.all_day:
        start, end = {"date": event.start.isoformat()}, {"date": event.end.isoformat()}
    else:
        start = {"dateTime": event.start.isoformat(), "timeZone": tz_name}
        end = {"dateTime": event.end.isoformat(), "timeZone": tz_name}
    return {
        "summary": event.summary,
        "location": event.location,
        "description": event.description,
        "start": start,
        "end": end,
        "extendedProperties": {"private": {"source": "ics", "ics_uid": event.uid}},
    }


def _same(existing: dict, body: dict, tz) -> bool:
    """Compare the fields we manage, ignoring formatting differences in timestamps."""
    try:
        old_bounds, new_bounds = event_bounds(existing, tz), event_bounds(body, tz)
    except (KeyError, ValueError):
        return False
    return (old_bounds == new_bounds and existing.get("summary") == body["summary"]
            and (existing.get("location") or "") == body["location"])


@tool(requires_approval=True, preview=_preview_import)
def import_ics(file_path: str) -> str:
    """Import a timetable from an .ics file (for example an ADE export) into the calendar. Safe to
    repeat: existing lessons are updated, not duplicated. The user must approve it with a button, so
    after calling this, say the import is waiting for their approval.

    Args:
        file_path: Full path of the .ics file, e.g. /Users/name/Downloads/timetable.ics
    """
    ctx = get_context()
    tz = ctx.config.tz
    _, result = _read_events(file_path)

    lo = min(_aware(e.start, tz) for e in result.events)
    hi = max(_aware(e.end, tz) for e in result.events)
    existing = {}
    for event in ctx.calendar.list_events(lo.isoformat(), hi.isoformat(), private_property=SOURCE_TAG):
        uid = event.get("extendedProperties", {}).get("private", {}).get("ics_uid")
        if uid:
            existing[uid] = event

    created = updated = unchanged = 0
    for ics_event in result.events:
        body = _body(ics_event, ctx.config.timezone)
        current = existing.get(ics_event.uid)
        if current is None:
            ctx.calendar.insert_event(body)
            created += 1
        elif _same(current, body, tz):
            unchanged += 1
        else:
            ctx.calendar.patch_event(current["id"], {k: v for k, v in body.items() if k != "extendedProperties"})
            updated += 1
    return f"Import finished: {created} new, {updated} updated, {unchanged} unchanged."


def _aware(value, tz):
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min, tzinfo=tz)
