"""Minimal iCalendar (.ics) reader for timetable exports such as ADE.

Why not the ``icalendar`` package? Timetable exports are flat lists of VEVENTs, so a
small reader covers them, keeps the dependency list short, and can be unit-tested
anywhere. Not supported: expanding RRULEs (ADE lists every occurrence separately),
VTIMEZONE definitions (IANA ``TZID`` names are used directly).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DURATION_RE = re.compile(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    summary: str
    location: str
    description: str
    start: "datetime | date"
    end: "datetime | date"

    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)


@dataclass
class ParseResult:
    events: list
    skipped: int = 0


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_property(line: str) -> Optional[tuple[str, dict[str, str], str]]:
    """'DTSTART;TZID=Europe/Paris:20261010T204500' -> ('DTSTART', {'TZID': 'Europe/Paris'}, '2026...')."""
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *raw_params = head.split(";")
    params = {}
    for item in raw_params:
        key, _, val = item.partition("=")
        params[key.upper()] = val.strip('"')
    return name.upper(), params, value


def _unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _parse_when(value: str, params: dict[str, str], default_tz: ZoneInfo):
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return naive.replace(tzinfo=timezone.utc).astimezone(default_tz)
    tzid = params.get("TZID")
    if tzid:
        try:
            return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone(default_tz)
        except (ZoneInfoNotFoundError, ValueError):
            pass  # unknown (e.g. Windows) zone name: fall back to the local zone
    return naive.replace(tzinfo=default_tz)


def _parse_duration(value: str) -> Optional[timedelta]:
    match = _DURATION_RE.match(value.strip())
    if not match or not any(match.groups()):
        return None
    weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_ics(text: str, default_tz: ZoneInfo) -> ParseResult:
    events: list[IcsEvent] = []
    skipped = 0
    current: Optional[dict] = None
    for line in _unfold(text):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            current = {}
            continue
        if upper == "END:VEVENT":
            if current is not None:
                event = _build(current, default_tz)
                if event is None:
                    skipped += 1
                else:
                    events.append(event)
            current = None
            continue
        if current is None:
            continue
        parsed = _split_property(line)
        if parsed:
            name, params, value = parsed
            current.setdefault(name, (params, value))  # first occurrence wins
    return ParseResult(events, skipped)


def _build(props: dict, tz: ZoneInfo) -> Optional[IcsEvent]:
    if props.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED":
        return None
    if "DTSTART" not in props:
        return None
    try:
        start = _parse_when(props["DTSTART"][1], props["DTSTART"][0], tz)
        if "DTEND" in props:
            end = _parse_when(props["DTEND"][1], props["DTEND"][0], tz)
        else:
            delta = _parse_duration(props["DURATION"][1]) if "DURATION" in props else None
            end = start + (delta or (timedelta(days=1) if not isinstance(start, datetime) else timedelta(hours=1)))
    except ValueError:
        return None
    if end <= start:
        return None
    summary = _unescape(props.get("SUMMARY", ({}, "Event"))[1]).strip() or "Event"
    uid = props.get("UID", ({}, ""))[1].strip()
    if not uid:  # stable fallback so re-imports still deduplicate
        uid = "gen-" + hashlib.sha1(f"{summary}|{start}".encode()).hexdigest()[:20]
    return IcsEvent(
        uid=uid,
        summary=summary,
        location=_unescape(props.get("LOCATION", ({}, ""))[1]).strip(),
        description=_unescape(props.get("DESCRIPTION", ({}, ""))[1]).strip(),
        start=start,
        end=end,
    )


__all__ = ["IcsEvent", "ParseResult", "parse_ics"]
