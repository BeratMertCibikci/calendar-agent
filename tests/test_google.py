import pytest

from calendar_agent.calendar_backends import GoogleCalendar
from calendar_agent.errors import EventNotFound, ToolError


class Resp:
    def __init__(self, status):
        self.status = status


class HttpError(Exception):
    def __init__(self, status):
        self.resp = Resp(status)


class Req:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeEvents:
    def __init__(self, pages=(), error=None):
        self.pages, self.error, self.params = list(pages), error, []

    def list(self, **kw):
        self.params.append(dict(kw))
        return Req(self.pages.pop(0))

    def get(self, **kw):
        return Req({"id": kw["eventId"]}, self.error)

    def delete(self, **kw):
        return Req({}, self.error)

    def patch(self, **kw):
        return Req({}, self.error)


class FakeService:
    def __init__(self, events):
        self._e = events

    def events(self):
        return self._e


def test_pagination_and_filters(config):
    ev = FakeEvents([{"items": [{"id": "1"}], "nextPageToken": "t"}, {"items": [{"id": "2"}]}])
    cal = GoogleCalendar(config, service=FakeService(ev))
    out = cal.list_events("a", "b", query="x", private_property="source=ics")
    assert [e["id"] for e in out] == ["1", "2"]
    assert ev.params[1]["pageToken"] == "t" and ev.params[0]["privateExtendedProperty"] == "source=ics"


@pytest.mark.parametrize("status", [404, 410])
def test_missing_event_maps_to_event_not_found(config, status):
    cal = GoogleCalendar(config, service=FakeService(FakeEvents(error=HttpError(status))))
    for fn in (lambda: cal.get_event("x"), lambda: cal.delete_event("x"), lambda: cal.patch_event("x", {})):
        with pytest.raises(EventNotFound):
            fn()


def test_other_errors_propagate(config):
    cal = GoogleCalendar(config, service=FakeService(FakeEvents(error=HttpError(500))))
    with pytest.raises(HttpError):
        cal.get_event("x")


def test_unauthorised_is_a_tool_error_not_a_browser_popup(config):
    cal = GoogleCalendar(config)
    with pytest.raises(ToolError):
        cal.service
