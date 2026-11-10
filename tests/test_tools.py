import pytest

from calendar_agent.registry import ToolRegistry, parse_docstring, tool


def day(h, m=0, d=12):
    return f"2026-11-{d:02d}T{h:02d}:{m:02d}:00"


def test_create_and_list(call):
    assert call("create_event", title="Lab", start=day(14), end=day(15)).startswith("Created: Lab")
    out = call("list_events", start=day(0), end=day(23, 59))
    assert "Lab" in out and "14:00-15:00" in out


def test_overlap_is_conflict_but_adjacent_is_not(call):
    call("create_event", title="A", start=day(14), end=day(15))
    clash = call("create_event", title="B", start=day(14, 30), end=day(15, 30))
    assert clash.startswith("CONFLICT") and "A" in clash
    assert call("create_event", title="C", start=day(15), end=day(16)).startswith("Created")


def test_force_creates_despite_conflict(call):
    call("create_event", title="A", start=day(14), end=day(15))
    assert call("create_event", title="B", start=day(14), end=day(15), force=True).startswith("Created")


def test_end_before_start_rejected(call):
    assert "end must be after" in call("create_event", title="X", start=day(15), end=day(14))


def test_bad_recurrence_and_reminder_rejected(call):
    assert "RRULE" in call("create_event", title="X", start=day(9), end=day(10), recurrence="weekly")
    assert "reminder_minutes" in call("create_event", title="X", start=day(9), end=day(10), reminder_minutes=-5)


def test_string_arguments_are_coerced(call):
    out = call("create_event", title="X", start=day(9), end=day(10), reminder_minutes="15", force="true")
    assert out.startswith("Created")


def test_update_keeps_duration(call, calendar):
    call("create_event", title="Lab", start=day(14), end=day(15, 30))
    out = call("update_event", event_id="evt1", start=day(16))
    assert "16:00-17:30" in out


def test_update_unknown_event_is_a_text_error(call):
    assert call("update_event", event_id="nope", title="x").startswith("Error")


def test_update_without_changes(call):
    call("create_event", title="Lab", start=day(14), end=day(15))
    assert "Nothing to change" in call("update_event", event_id="evt1")


def test_delete_is_parked_not_executed(call, calendar, store):
    call("create_event", title="Lab", start=day(14), end=day(15))
    out = call("delete_event", event_id="evt1")
    assert "APPROVAL REQUIRED" in out and len(calendar.list_events(day(0), day(23, 59))) == 1
    assert len(store.pending_for_session("sess-0001")) == 1


def test_delete_preview_for_unknown_event_errors_instead_of_parking(call, store):
    assert call("delete_event", event_id="ghost").startswith("Error")
    assert store.pending_for_session("sess-0001") == []


def test_delete_runs_when_approved(call, calendar):
    call("create_event", title="Lab", start=day(14), end=day(15))
    assert call("delete_event", approved=True, event_id="evt1").startswith("Deleted")
    assert calendar.list_events(day(0), day(23, 59)) == []


def test_free_slots_between_events(call):
    call("create_event", title="A", start=day(10), end=day(12))
    call("create_event", title="B", start=day(14), end=day(15))
    out = call("find_free_slots", start_date="2026-11-12", end_date="2026-11-12", min_minutes=60)
    assert "09:00" in out and "10:00" in out and "12:00-14:00" in out.replace(" ", "") or "12:00" in out
    assert "15:00" in out


def test_free_slots_limits(call):
    assert "at most" in call("find_free_slots", start_date="2026-11-01", end_date="2026-12-30")
    assert "end_date" in call("find_free_slots", start_date="2026-11-05", end_date="2026-11-01")


def test_tasks_roundtrip(call):
    assert "added" in call("add_task", text="Buy ticket")
    assert "Buy ticket" in call("list_tasks")
    assert "done" in call("complete_task", task_id=1)
    assert call("list_tasks") == "No tasks."
    assert "No open task" in call("complete_task", task_id=1)


def test_preferences_limits_and_prompt(call, ctx):
    from calendar_agent.prompts import build_system_prompt
    assert "Saved" in call("remember", fact="Meetings last 90 minutes")
    prompt = build_system_prompt(ctx.config, ctx.now(), ctx.store.list_preferences())
    assert "Meetings last 90 minutes" in prompt and "[#1]" in prompt
    assert call("remember", fact="x" * 5000).startswith("Error")
    assert "removed" in call("forget", preference_id=1)


def test_web_search_wraps_results_as_untrusted(ctx):
    from calendar_agent.registry import registry
    ctx.search = lambda q, n=5: [{"title": "PSG", "href": "http://x", "body": "20:45 kickoff"}]
    out = registry.call("web_search", {"query": "psg"})
    assert "20:45" in out and "untrusted" in out.lower()


# ---- registry ---------------------------------------------------------------------------------
def test_docstring_parser():
    def f(a: int, b: str = "x"):
        """Summary line.

        Args:
            a: first thing
            b: second
                thing continues
        """
    summary, args = parse_docstring(f.__doc__)
    assert summary == "Summary line." and args["a"] == "first thing" and "continues" in args["b"]


def test_schema_generation_and_unknown_args():
    reg = ToolRegistry()

    @tool
    def add(a: int, b: int = 2, flag: bool = False) -> str:
        """Add numbers.

        Args:
            a: first
            b: second
            flag: a flag
        """
        return str(a + b)

    reg.register(add)
    schema = reg.json_schemas()[0]["function"]
    assert schema["name"] == "add" and schema["parameters"]["required"] == ["a"]
    assert schema["parameters"]["properties"]["a"]["type"] == "integer"
    assert reg.call("add", {"a": "5", "b": None}) == "7"
    assert "unknown argument" in reg.call("add", {"a": 1, "zzz": 2}).lower()
    assert "missing" in reg.call("add", {}).lower()
    assert "unknown tool" in reg.call("nope", {}).lower()
    assert reg.call("add", {"a": "abc"}).startswith("Error")


def test_approval_tool_needs_preview():
    reg = ToolRegistry()

    def danger() -> str:
        """Danger."""
        return "x"

    with pytest.raises(Exception):
        reg.register(danger, requires_approval=True)
