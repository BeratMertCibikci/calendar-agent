"""Calendar backends.

Tools talk to a small interface instead of the Google client, which gives us
  * a real backend (Google Calendar) and an in-memory one for demos and tests,
  * one place that knows the Google API shapes (pagination, 404 handling, token
    storage), so the tools stay readable.

Events are plain dicts in the Google Calendar format (``summary``, ``start``,
``end``, ``extendedProperties`` ...).
"""
from __future__ import annotations

import itertools
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import Config
from .errors import EventNotFound
from .google_auth import build_service, http_status
from .timeutil import event_bounds, parse_dt


class CalendarBackend(Protocol):
    def list_events(self, time_min: str, time_max: str, query: str = "", private_property: str = "") -> list[dict]: ...
    def get_event(self, event_id: str) -> dict: ...
    def insert_event(self, body: dict) -> dict: ...
    def patch_event(self, event_id: str, body: dict) -> dict: ...
    def delete_event(self, event_id: str) -> None: ...


# ---------------------------------------------------------------------------- memory

class InMemoryCalendar:
    """Throwaway calendar. Recurrence rules are stored but not expanded."""

    def __init__(self, tz: ZoneInfo):
        self.tz = tz
        self._events: dict[str, dict] = {}
        self._ids = itertools.count(1)

    def list_events(self, time_min, time_max, query="", private_property=""):
        lo, hi = parse_dt(time_min, self.tz), parse_dt(time_max, self.tz)
        out = []
        for event in self._events.values():
            start, end = event_bounds(event, self.tz)
            if not (start < hi and end > lo):  # same overlap rule as Google
                continue
            if query:
                haystack = " ".join(str(event.get(k, "")) for k in ("summary", "description", "location")).lower()
                if query.lower() not in haystack:
                    continue
            if private_property:
                key, _, value = private_property.partition("=")
                if event.get("extendedProperties", {}).get("private", {}).get(key) != value:
                    continue
            out.append(event)
        return sorted((dict(e) for e in out), key=lambda e: event_bounds(e, self.tz)[0])

    def get_event(self, event_id):
        if event_id not in self._events:
            raise EventNotFound(event_id)
        return dict(self._events[event_id])

    def insert_event(self, body):
        event_id = f"evt{next(self._ids)}"
        event = {**body, "id": event_id, "htmlLink": f"memory://{event_id}"}
        self._events[event_id] = event
        return dict(event)

    def patch_event(self, event_id, body):
        if event_id not in self._events:
            raise EventNotFound(event_id)
        self._events[event_id].update(body)
        return dict(self._events[event_id])

    def delete_event(self, event_id):
        if event_id not in self._events:
            raise EventNotFound(event_id)
        del self._events[event_id]


# ---------------------------------------------------------------------------- google

class GoogleCalendar:
    """Google Calendar v3 behind the CalendarBackend interface.

    The service is built lazily, and it never opens a browser by itself: if there is
    no usable token it raises a ToolError telling the user to run ``calendar-agent auth``.
    (The first prototype launched the OAuth flow inside a chat request, which hangs the request.)
    """

    def __init__(self, config: Config, service=None):
        self.config = config
        self._service = service

    # -- auth ----------------------------------------------------------------
    def authorize(self, interactive: bool = False):
        self._service = build_service(self.config, "calendar", "v3", interactive)
        return self._service

    @property
    def service(self):
        return self._service or self.authorize()

    # -- interface -------------------------------------------------------------
    def list_events(self, time_min, time_max, query="", private_property=""):
        params = dict(calendarId="primary", timeMin=time_min, timeMax=time_max,
                      singleEvents=True, orderBy="startTime", maxResults=250)
        if query:
            params["q"] = query
        if private_property:
            params["privateExtendedProperty"] = private_property
        items: list[dict] = []
        while True:
            page = self.service.events().list(**params).execute()
            items.extend(page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                return items
            params["pageToken"] = token

    def get_event(self, event_id):
        try:
            return self.service.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) in (404, 410):
                raise EventNotFound(event_id) from None
            raise

    def insert_event(self, body):
        return self.service.events().insert(calendarId="primary", body=body).execute()

    def patch_event(self, event_id, body):
        try:
            return self.service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) in (404, 410):
                raise EventNotFound(event_id) from None
            raise

    def delete_event(self, event_id):
        try:
            self.service.events().delete(calendarId="primary", eventId=event_id).execute()
        except Exception as exc:  # noqa: BLE001
            if http_status(exc) in (404, 410):
                raise EventNotFound(event_id) from None
            raise


def build_calendar(config: Config) -> CalendarBackend:
    if config.calendar == "memory":
        return InMemoryCalendar(config.tz)
    return GoogleCalendar(config)


__all__ = ["CalendarBackend", "InMemoryCalendar", "GoogleCalendar", "build_calendar"]
