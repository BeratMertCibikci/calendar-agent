"""Desktop window: the same Flask app, shown in a native window instead of a browser tab.

``calendar-agent app`` starts the server on a free local port in a background thread and opens a
pywebview window pointing at it. Closing the window stops the server. Nothing about the security model
changes: the server still binds to 127.0.0.1 only, and the CSRF token is injected into the page as before.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from werkzeug.serving import make_server

from .config import Config
from .errors import ToolError

log = logging.getLogger("calendar_agent.desktop")

APP_NAME = "Calendar Agent"
WINDOW_SIZE = (560, 820)
MIN_SIZE = (380, 520)


def default_data_dir() -> Path:
    """Per-user folder for the packaged app (a double-clicked .app has no meaningful working directory)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "calendar-agent"


def pick_port(host: str, preferred: int) -> int:
    """The configured port if it is free, otherwise any free one (the app is opened by us, not typed)."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise OSError("No free local port available.")


def _import_webview() -> Any:
    try:
        import webview
    except ImportError:
        raise ToolError("The desktop window needs pywebview. Install it: pip install 'calendar-agent[app]'") from None
    return webview


def run_app(config: Config, webview_module: Optional[Any] = None) -> int:
    """Start the server and show the window. ``webview_module`` is injectable for tests."""
    webview = webview_module or _import_webview()
    from .server import create_app

    port = pick_port(config.host, config.port)
    server = make_server(config.host, port, create_app(config), threaded=True)  # binds now: no race with the window
    thread = threading.Thread(target=server.serve_forever, name="calendar-agent-server", daemon=True)
    thread.start()
    url = f"http://{config.host}:{port}/"
    log.info("window -> %s", url)
    try:
        webview.create_window(APP_NAME, url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=MIN_SIZE)
        webview.start()  # blocks until the window is closed
    finally:
        server.shutdown()
        server.server_close()
    return 0
