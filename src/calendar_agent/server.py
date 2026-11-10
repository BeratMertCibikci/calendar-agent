"""Flask app: JSON API + the single-page UI.

Hardening that matters for a local server that can touch your calendar:
  * Host header allow-list (blocks DNS-rebinding),
  * Origin check and a per-process CSRF token on every POST,
  * JSON-only bodies (a cross-site <form> cannot send application/json),
  * approvals are executed by this server when the user clicks, never by the model.
"""
from __future__ import annotations

import base64
import binascii
import hmac
import logging
import re
import secrets
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from . import tools  # noqa: F401  (importing registers the tools)
from .calendar_backends import build_calendar
from .config import Config
from .context import Context, init_context
from .db import Store
from .engines import BaseEngine, build_engine
from .errors import EngineUnavailable
from .registry import registry, session_scope

log = logging.getLogger("calendar_agent.server")

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_MESSAGE_CHARS = 4000
NOTICE_SEPARATOR = "\n\nUser message:\n"
STATIC_DIR = Path(__file__).parent / "static"


def _json_error(message: str, status: int):
    return jsonify(error=message), status


def _clean_image(raw) -> Optional[dict]:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("image must be an object")
    mime, data = raw.get("mime_type"), raw.get("data")
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Unsupported image type (use JPEG, PNG, WebP or GIF).")
    if not isinstance(data, str):
        raise ValueError("image data must be a base64 string")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("image data is not valid base64") from None
    if not decoded or len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError("The image is empty or larger than 6 MB.")
    return {"mime_type": mime, "data": data}


def create_app(
    config: Optional[Config] = None,
    *,
    engine: Optional[BaseEngine] = None,
    store: Optional[Store] = None,
    calendar=None,
    search: Optional[Callable] = None,
    clock: Optional[Callable] = None,
) -> Flask:
    config = config or Config.from_env()
    store = store or Store(config.db_path, action_ttl=config.action_ttl_seconds)
    calendar = calendar or build_calendar(config)
    init_context(Context(config=config, store=store, calendar=calendar, search=search, clock=clock))
    engine = engine or build_engine(config, store, registry)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    csrf_token = secrets.token_urlsafe(32)
    port = config.port

    # ------------------------------------------------------------------ guards
    @app.before_request
    def guard():
        host = request.host.split(":")[0].strip("[]").lower()
        local_names = {"localhost", "127.0.0.1", "::1", config.host.lower()}
        if host not in local_names:
            return _json_error("Unexpected Host header.", 403)
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                return _json_error("Cross-origin request refused.", 403)
            sent = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(sent, csrf_token):
                return _json_error("Missing or invalid CSRF token. Reload the page.", 403)
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    for code, text in ((400, "Bad request."), (404, "Not found."), (405, "Method not allowed."),
                       (413, "Request too large."), (415, "Send JSON (Content-Type: application/json).")):
        app.register_error_handler(code, lambda _e, c=code, t=text: _json_error(t, c))

    # -------------------------------------------------------------------- pages
    @app.get("/")
    def index():
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        html = (html.replace("__CSRF__", csrf_token).replace("__LANG__", config.language)
                .replace("__ENGINE__", engine.name))
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, engine=engine.name, calendar=config.calendar, tools=registry.names())

    @app.get("/api/session/<session_id>")
    def session_state(session_id: str):
        if not SESSION_RE.match(session_id):
            return _json_error("Invalid session id.", 400)
        return jsonify(transcript=store.get_transcript(session_id), pending=store.pending_for_session(session_id))

    # --------------------------------------------------------------------- chat
    def _session(data) -> str:
        sid = (data or {}).get("session_id", "")
        if not isinstance(sid, str) or not SESSION_RE.match(sid):
            raise ValueError("Invalid session id.")
        return sid

    @app.post("/api/chat")
    def chat():
        data = request.get_json()
        try:
            sid = _session(data)
            image = _clean_image(data.get("image"))
        except ValueError as exc:
            return _json_error(str(exc), 400)
        text = str(data.get("message") or "").strip()[:MAX_MESSAGE_CHARS]
        if not text and not image:
            return _json_error("Empty message.", 400)

        notes = store.pop_notes(sid)
        engine_message = text
        if notes:
            engine_message = "[System notice from the application, not from the user: " + " ".join(notes) + "]" \
                             + NOTICE_SEPARATOR + text
        store.add_transcript(sid, "user", text, has_image=bool(image))
        try:
            with session_scope(sid):
                reply = engine.reply(sid, engine_message, image)
        except EngineUnavailable as exc:
            for note in notes:  # not delivered: keep it for the next attempt
                store.add_note(sid, note)
            return _json_error(str(exc), 503)
        except Exception:  # noqa: BLE001
            log.exception("engine failed")
            for note in notes:
                store.add_note(sid, note)
            return _json_error("The assistant hit an unexpected error. Details are in the server log.", 500)

        store.add_transcript(sid, "assistant", reply)
        return jsonify(reply=reply, pending=store.pending_for_session(sid))

    # ---------------------------------------------------------------- approvals
    @app.post("/api/actions/<int:action_id>/approve")
    def approve(action_id: int):
        try:
            sid = _session(request.get_json())
        except ValueError as exc:
            return _json_error(str(exc), 400)
        action = store.claim_action(action_id, sid)
        if action is None:
            return _json_error("This action is no longer pending (already handled, expired, or not yours).", 409)
        with session_scope(sid):
            result = registry.call(action["tool"], action["args"], approved=True, session_id=sid)
        ok = not result.startswith("Error:")
        store.finish_action(action_id, "done" if ok else "failed", result)
        store.add_note(sid, f"The user APPROVED action #{action_id} ({action['summary']}). Result: {result}")
        store.add_transcript(sid, "assistant", ("✅ " if ok else "⚠️ ") + result)
        return jsonify(ok=ok, result=result, pending=store.pending_for_session(sid))

    @app.post("/api/actions/<int:action_id>/reject")
    def reject(action_id: int):
        try:
            sid = _session(request.get_json())
        except ValueError as exc:
            return _json_error(str(exc), 400)
        action = store.reject_action(action_id, sid)
        if action is None:
            return _json_error("This action is no longer pending.", 409)
        store.add_note(sid, f"The user REJECTED action #{action_id} ({action['summary']}). Nothing was executed.")
        store.add_transcript(sid, "assistant", f"❌ {action['summary']}")
        return jsonify(ok=True, pending=store.pending_for_session(sid))

    log.info("engine=%s calendar=%s port=%s", engine.name, config.calendar, port)
    return app
