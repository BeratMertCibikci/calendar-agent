import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from calendar_agent.calendar_backends import InMemoryCalendar
from calendar_agent.config import Config
from calendar_agent.context import Context, init_context, reset_context
from calendar_agent.db import Store
from calendar_agent import tools  # noqa: F401
from calendar_agent.registry import registry

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)


@pytest.fixture
def config(tmp_path):
    return Config.from_env({"ENGINE": "demo", "CALENDAR_BACKEND": "memory", "DATA_DIR": str(tmp_path),
                            "ICS_ALLOWED_DIRS": str(tmp_path)})


@pytest.fixture
def store(config):
    return Store(config.db_path, action_ttl=config.action_ttl_seconds)


@pytest.fixture
def calendar(config):
    return InMemoryCalendar(config.tz)


@pytest.fixture
def ctx(config, store, calendar):
    c = init_context(Context(config=config, store=store, calendar=calendar, clock=lambda: NOW))
    yield c
    reset_context()


@pytest.fixture
def call(ctx):
    def _call(name, approved=False, session_id="sess-0001", **kw):
        return registry.call(name, kw, approved=approved, session_id=session_id)
    return _call


@pytest.fixture
def client(config, store, calendar):
    from calendar_agent.server import create_app
    app = create_app(config, store=store, calendar=calendar, clock=lambda: NOW)
    c = app.test_client()
    html = c.get("/").get_data(as_text=True)
    token = re.search(r'csrf-token" content="([^"]+)', html).group(1)
    c.token = token
    c.sid = "abcdefgh12345678"

    def post(path, body=None, token_=True, **kw):
        headers = {"X-CSRF-Token": token} if token_ else {}
        headers.update(kw.pop("headers", {}))
        return c.post(path, json=body, headers=headers, **kw)
    c.jpost = post
    return c
