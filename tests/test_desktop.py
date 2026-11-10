import socket
import sys
import types
import urllib.request

import pytest

from calendar_agent import desktop
from calendar_agent.errors import EngineUnavailable, ToolError


class FakeWebview(types.SimpleNamespace):
    """Stands in for pywebview: records the window and, on start(), behaves like a user who opened it."""

    def __init__(self):
        super().__init__(windows=[], page=None, status=None)

    def create_window(self, title, url, **kw):
        self.windows.append((title, url, kw))

    def start(self):
        url = self.windows[0][1]
        with urllib.request.urlopen(url, timeout=5) as response:
            self.status, self.page = response.status, response.read().decode()


def test_run_app_serves_the_ui_and_stops_the_server(config):
    fake = FakeWebview()
    assert desktop.run_app(config, webview_module=fake) == 0
    title, url, kw = fake.windows[0]
    assert title == "Calendar Agent" and url.startswith("http://127.0.0.1:") and kw["min_size"]
    assert fake.status == 200 and "csrf-token" in fake.page and "__CSRF__" not in fake.page
    port = int(url.rsplit(":", 1)[1].strip("/"))
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0  # closed window -> server is gone


def test_pick_port_falls_back_when_busy():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        taken = busy.getsockname()[1]
        assert desktop.pick_port("127.0.0.1", taken) != taken


def test_missing_pywebview_gives_install_hint(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # makes `import webview` raise ImportError
    with pytest.raises(ToolError, match=r"calendar-agent\[app\]"):
        desktop.run_app(config)


def test_default_data_dir_is_per_user():
    path = desktop.default_data_dir()
    assert path.is_absolute() and ("Calendar" in str(path) or "calendar-agent" in str(path))


def test_cli_app_command_sets_per_user_data_dir(monkeypatch, tmp_path):
    from calendar_agent import __main__ as cli
    seen = {}
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(desktop, "run_app", lambda config: seen.setdefault("data_dir", config.data_dir) and 0)
    monkeypatch.setenv("ENGINE", "demo")
    monkeypatch.setenv("CALENDAR_BACKEND", "memory")
    assert cli.main(["app"]) == 0
    assert str(seen["data_dir"]).startswith(str(tmp_path))


def test_gemini_without_key_is_a_clear_message(config, store, monkeypatch):
    from calendar_agent.engines.gemini import GeminiEngine
    from calendar_agent.registry import registry

    class FakeGenai(types.SimpleNamespace):
        @staticmethod
        def Client():
            raise ValueError("Missing key inputs argument!")

    google = types.ModuleType("google")
    google.genai = FakeGenai()
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", google.genai)
    with pytest.raises(EngineUnavailable, match="GEMINI_API_KEY"):
        GeminiEngine(config, store, registry).client
