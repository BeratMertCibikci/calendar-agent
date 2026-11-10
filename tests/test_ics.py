from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_agent.icsparse import parse_ics

TZ = ZoneInfo("Europe/Paris")
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a1
SUMMARY:Machine Learning
LOCATION:Salle 12
DTSTART;TZID=Europe/Paris:20261112T090000
DTEND;TZID=Europe/Paris:20261112T103000
END:VEVENT
BEGIN:VEVENT
UID:a2
SUMMARY:Long title that is
 folded over lines
DTSTART:20261113T080000Z
DURATION:PT2H
END:VEVENT
BEGIN:VEVENT
UID:a3
SUMMARY:Cancelled
STATUS:CANCELLED
DTSTART:20261114T080000Z
DTEND:20261114T090000Z
END:VEVENT
BEGIN:VEVENT
UID:a4
SUMMARY:Holiday
DTSTART;VALUE=DATE:20261111
DTEND;VALUE=DATE:20261112
END:VEVENT
END:VCALENDAR
"""


def test_parse_basic_fields():
    res = parse_ics(ICS, TZ)
    by = {e.uid: e for e in res.events}
    assert set(by) == {"a1", "a2", "a4"} and res.skipped == 1
    assert by["a1"].start == datetime(2026, 11, 12, 9, 0, tzinfo=TZ) and by["a1"].location == "Salle 12"
    assert by["a2"].summary == "Long title that isfolded over lines"
    assert (by["a2"].end - by["a2"].start).total_seconds() == 7200
    assert by["a4"].all_day


def test_garbage_does_not_raise():
    assert parse_ics("not an ics", TZ).events == []


def test_missing_uid_gets_stable_fallback():
    text = "BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20261112T090000Z\nDTEND:20261112T100000Z\nEND:VEVENT"
    a, b = parse_ics(text, TZ).events[0], parse_ics(text, TZ).events[0]
    assert a.uid and a.uid == b.uid
