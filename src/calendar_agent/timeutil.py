"""Date/time helpers shared by the calendar tools."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import ToolError


def parse_dt(value: str, tz: ZoneInfo) -> datetime:
    """ISO 8601 string -> aware datetime. Naive input is read as local time in ``tz``."""
    text = (value or "").strip()
    if not text:
        raise ToolError("Empty date/time. Use ISO 8601, e.g. 2026-10-02T15:00:00.")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ToolError(f"Invalid date/time {value!r}. Use ISO 8601, e.g. 2026-10-02T15:00:00.") from None
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        raise ToolError(f"Invalid date {value!r}. Use YYYY-MM-DD.") from None


def parse_hhmm(value: str) -> time:
    try:
        hour, minute = (value or "").strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        raise ToolError(f"Invalid time {value!r}. Use HH:MM, e.g. 09:00.") from None


def is_all_day(event: dict) -> bool:
    return "dateTime" not in event.get("start", {})


def event_bounds(event: dict, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """(start, end) as aware datetimes. All-day events span local midnight to midnight."""
    def one(part: dict) -> datetime:
        if "dateTime" in part:
            return parse_dt(part["dateTime"], tz)
        return datetime.combine(date.fromisoformat(part["date"]), time.min, tzinfo=tz)
    return one(event["start"]), one(event["end"])


def describe_event(event: dict, tz: ZoneInfo) -> str:
    """One line the model can read back: title | when | id."""
    start, end = event_bounds(event, tz)
    title = event.get("summary") or "(untitled)"
    if is_all_day(event):
        when = f"{start:%Y-%m-%d} (all day)"
    elif start.date() == end.date():
        when = f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
    else:
        when = f"{start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M}"
    return f"{title} | {when} | id={event['id']}"


def round_up(dt: datetime, minutes: int = 5) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % minutes
    return dt + timedelta(minutes=extra)
