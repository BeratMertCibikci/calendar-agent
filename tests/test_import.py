from calendar_agent.registry import registry

from test_ics import ICS


def write(tmp_path, text=ICS, name="plan.ics"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def listing(cal):
    return cal.list_events("2026-01-01T00:00:00+01:00", "2027-01-01T00:00:00+01:00")


def test_import_is_idempotent_and_updates(call, calendar, tmp_path):
    path = write(tmp_path)
    assert "APPROVAL" in call("import_ics", file_path=path)
    assert listing(calendar) == []  # nothing before approval
    assert "3 new" in call("import_ics", approved=True, file_path=path)
    assert len(listing(calendar)) == 3
    assert "0 new, 0 updated, 3 unchanged" in call("import_ics", approved=True, file_path=path)
    changed = write(tmp_path, ICS.replace("Salle 12", "Salle 99"))
    assert "1 updated" in call("import_ics", approved=True, file_path=changed)
    assert len(listing(calendar)) == 3


def test_path_outside_allowlist_refused(call, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "x.ics"
    other.write_text(ICS)
    assert call("import_ics", file_path=str(other)).startswith("Error")


def test_non_ics_and_missing_files_refused(call, tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    assert call("import_ics", file_path=str(txt)).startswith("Error")
    assert call("import_ics", file_path=str(tmp_path / "nope.ics")).startswith("Error")
