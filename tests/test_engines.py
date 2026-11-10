import pytest

from calendar_agent.engines.base import trim_history
from calendar_agent.engines.gemini import GeminiEngine
from calendar_agent.engines.ollama import OllamaEngine
from calendar_agent.errors import EngineUnavailable
from calendar_agent.registry import registry


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code}")
        self.code = code


def make_gemini(config, store, script):
    """script: list of results/exceptions returned by successive _attempt calls."""
    engine = GeminiEngine(config, store, registry, sleep=lambda s: None)
    calls = []

    def attempt(model, system, past, message, image):
        calls.append(model)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    engine._attempt = attempt
    return engine, calls


def test_gemini_retries_then_succeeds(config, store, ctx):
    engine, calls = make_gemini(config, store, [ApiError(503), ("hello", [{"role": "user", "parts": [{"text": "hi"}]}])])
    assert engine.reply("sess-0001", "hi") == "hello"
    assert len(set(calls)) == 1 and len(calls) == 2


def test_gemini_falls_back_on_404_and_after_retries(config, store, ctx):
    engine, calls = make_gemini(config, store, [ApiError(404), ApiError(503), ApiError(503), ("ok", [])])
    assert engine.reply("sess-0001", "hi") == "ok"
    assert calls[0] != calls[1] and calls[1] == calls[2] and calls[3] != calls[2]


def test_gemini_all_models_down(config, store, ctx):
    engine, _ = make_gemini(config, store, [ApiError(503)] * 20)
    with pytest.raises(EngineUnavailable):
        engine.reply("sess-0001", "hi")


def test_gemini_does_not_retry_client_errors(config, store, ctx):
    engine, calls = make_gemini(config, store, [ApiError(400)])
    with pytest.raises(ApiError):
        engine.reply("sess-0001", "hi")
    assert len(calls) == 1


def test_gemini_history_saved_and_images_stripped(config, store, ctx):
    hist = [{"role": "user", "parts": [{"inline_data": {"data": "AAAA"}}, {"text": "see"}]},
            {"role": "model", "parts": [{"text": "ok"}]}]
    engine, _ = make_gemini(config, store, [("ok", hist)])
    engine.reply("sess-0001", "see")
    saved = engine.load("sess-0001")
    assert "inline_data" not in str(saved) and "[image omitted]" in str(saved)


def test_corrupt_history_is_ignored(config, store):
    with store._conn() as con:
        con.execute("INSERT INTO conversations (session_id, engine, history, updated) VALUES (?,?,?,?)",
                    ("sess-0001", "gemini", "{not json", "2026-01-01"))
    engine, _ = make_gemini(config, store, [])
    assert engine.load("sess-0001") == []


def test_trim_history_cuts_at_user_turn():
    h = [{"role": "user", "parts": [{"text": "a"}]}, {"role": "model", "parts": [{"function_call": {}}]},
         {"role": "user", "parts": [{"function_response": {}}]}, {"role": "model", "parts": [{"text": "r"}]},
         {"role": "user", "parts": [{"text": "b"}]}, {"role": "model", "parts": [{"text": "s"}]}]
    engine = GeminiEngine.__new__(GeminiEngine)
    out = trim_history(h, 3, engine.is_turn_start)
    assert out[0]["parts"][0]["text"] == "b"


class FakeMessage(dict):
    def model_dump(self):
        return dict(self)


def test_ollama_tool_loop(config, store, ctx, calendar):
    answers = [
        {"message": FakeMessage(role="assistant", content="", tool_calls=[{"function": {
            "name": "create_event",
            "arguments": {"title": "Lab", "start": "2026-11-12T14:00:00", "end": "2026-11-12T15:00:00",
                          "reminder_minutes": None}}}])},
        {"message": FakeMessage(role="assistant", content="Added.")},
    ]
    seen = []

    def chat(**kw):
        seen.append(kw["messages"][-1])
        return answers.pop(0)

    engine = OllamaEngine(config, store, registry, chat_fn=chat)
    assert engine.reply("sess-0001", "add lab") == "Added."
    assert seen[1]["role"] == "tool" and "Created" in seen[1]["content"]
    assert len(calendar.list_events("2026-11-12T00:00:00+01:00", "2026-11-13T00:00:00+01:00")) == 1


def test_ollama_bounded_rounds(config, store, ctx):
    loop = {"message": FakeMessage(role="assistant", content="", tool_calls=[
        {"function": {"name": "list_tasks", "arguments": {}}}])}
    engine = OllamaEngine(config, store, registry, chat_fn=lambda **kw: loop)
    assert "several steps" in engine.reply("sess-0001", "x")


def test_ollama_down(config, store, ctx):
    def chat(**kw):
        raise ConnectionError()
    with pytest.raises(EngineUnavailable):
        OllamaEngine(config, store, registry, chat_fn=chat).reply("sess-0001", "x")
