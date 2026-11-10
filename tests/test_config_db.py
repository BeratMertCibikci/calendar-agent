import pytest

from calendar_agent.config import Config, ConfigError
from calendar_agent.db import Store


def test_defaults(tmp_path):
    c = Config.from_env({"DATA_DIR": str(tmp_path)})
    assert c.host == "127.0.0.1" and c.port == 5001 and not c.debug and c.engine == "gemini"


@pytest.mark.parametrize("env", [{"ENGINE": "gpt"}, {"CALENDAR_BACKEND": "x"}, {"TIMEZONE": "Mars/Base"},
                                 {"ASSISTANT_LANGUAGE": "xx"}, {"PORT": "99999"}, {"PORT": "abc"}])
def test_invalid_config(env):
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_gemini_models_override():
    assert Config.from_env({"GEMINI_MODELS": "a, b"}).gemini_models == ("a", "b")


def test_preferences_limit(store):
    for i in range(50):
        store.add_preference(f"fact {i}")
    with pytest.raises(ValueError):
        store.add_preference("one too many")


def test_old_schema_tables_compatible(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, done INTEGER DEFAULT 0)")
    con.execute("INSERT INTO tasks (text) VALUES ('legacy')")
    con.commit(); con.close()
    assert Store(path).list_tasks()[0]["text"] == "legacy"


def test_action_claim_is_atomic(store):
    aid = store.create_action("sess-0001", "t", {}, "s")
    assert store.claim_action(aid, "sess-0001") is not None
    assert store.claim_action(aid, "sess-0001") is None
