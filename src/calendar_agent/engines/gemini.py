"""Gemini engine (cloud).

The google-genai SDK runs the tool loop itself ("automatic function calling"): we hand it
the registry's callables and it calls them, feeds results back and returns the final text.
Our job is everything around that: model fallback, retries, history persistence.

Retrying note: an overload (503) can arrive *after* a tool already ran. That is safe here
because the tools that matter are idempotent or gated: create_event re-checks for
conflicts, and delete/import only create a pending approval, never act directly.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Optional

from ..errors import EngineUnavailable
from .base import IMAGE_PLACEHOLDER, NOT_FOUND_CODES, RETRYABLE_CODES, BaseEngine, error_code

log = logging.getLogger("calendar_agent.engine.gemini")


class GeminiEngine(BaseEngine):
    name = "gemini"

    def __init__(self, config, store, registry, client=None, sleep=time.sleep, attempts_per_model: int = 2):
        super().__init__(config, store, registry)
        self._client = client
        self.sleep = sleep
        self.attempts_per_model = attempts_per_model

    # -- SDK access (kept in one place so the rest is testable without the SDK) ----------
    @property
    def client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError:
                raise EngineUnavailable("Gemini SDK missing. Install it: pip install 'calendar-agent[gemini]'") from None
            try:
                self._client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
            except ValueError:  # the SDK raises this when no key is configured
                raise EngineUnavailable("No Gemini API key found. Add GEMINI_API_KEY=... to the .env file (the desktop app "
                                        f"reads {self.config.data_dir / '.env'}), or use ENGINE=ollama / ENGINE=demo.") from None
        return self._client

    def _attempt(self, model: str, system: str, past: list, message: str, image: Optional[dict]):
        """One model call. Returns (reply_text, new_history_as_json_dicts)."""
        from google.genai import types

        content: Any = message
        if image:
            part = types.Part.from_bytes(data=base64.b64decode(image["data"]), mime_type=image["mime_type"])
            content = [part, message] if message else [part]
        config = types.GenerateContentConfig(system_instruction=system, tools=self.registry.callables())
        # validate_json pairs with model_dump(mode="json"): bytes fields (thought signatures) round-trip as base64.
        history = [types.Content.model_validate_json(json.dumps(item)) for item in past]
        chat = self.client.chats.create(model=model, config=config, history=history)
        response = chat.send_message(content)
        new_history = [c.model_dump(mode="json", exclude_none=True) for c in chat.get_history()]
        return response.text or "", new_history

    # -- engine interface ---------------------------------------------------------------------
    def reply(self, session_id: str, message: str, image: Optional[dict] = None) -> str:
        past = self.load(session_id)
        system = self.system_prompt()
        last_error: Optional[BaseException] = None
        for model in self.config.gemini_models:
            for attempt in range(self.attempts_per_model):
                try:
                    text, new_history = self._attempt(model, system, past, message, image)
                except EngineUnavailable:
                    raise
                except Exception as exc:  # noqa: BLE001 - classified below
                    code = error_code(exc)
                    last_error = exc
                    if code in NOT_FOUND_CODES:
                        log.warning("model %s not available (404), trying the next one", model)
                        break
                    if code in RETRYABLE_CODES:
                        log.warning("model %s busy (%s), attempt %d", model, code, attempt + 1)
                        self.sleep(1 + 2 * attempt)
                        continue
                    raise  # 400/401/403 and bugs: retrying will not help
                self.save(session_id, new_history)
                log.info("answered by %s", model)
                return text or "Done."
        raise EngineUnavailable(
            "Gemini is overloaded or the configured models are unavailable. Try again in a moment, or edit "
            "GEMINI_MODELS."
        ) from last_error

    # -- history format -----------------------------------------------------------------------
    def is_turn_start(self, message: dict) -> bool:
        parts = message.get("parts") or []
        return (message.get("role") == "user"
                and any("text" in p for p in parts) and not any("function_response" in p for p in parts))

    def strip_images(self, history: list) -> list:
        """Inline image bytes are huge and would be re-sent on every turn; keep a marker instead."""
        out = []
        for message in history:
            parts = []
            for part in message.get("parts") or []:
                parts.append({"text": IMAGE_PLACEHOLDER} if "inline_data" in part else part)
            out.append({**message, "parts": parts})
        return out
