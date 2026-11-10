"""Shared engine plumbing: the engine interface, error classification, history hygiene."""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..config import Config
from ..context import get_context
from ..db import Store
from ..prompts import build_system_prompt
from ..registry import ToolRegistry

RETRYABLE_CODES = {429, 500, 502, 503, 504}
NOT_FOUND_CODES = {404}
IMAGE_PLACEHOLDER = "[image omitted]"


def error_code(exc: BaseException) -> Optional[int]:
    """HTTP-like status of an SDK exception, whatever the SDK calls the attribute."""
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


class BaseEngine:
    name = "base"

    def __init__(self, config: Config, store: Store, registry: ToolRegistry):
        self.config, self.store, self.registry = config, store, registry

    def system_prompt(self) -> str:
        ctx = get_context()
        return build_system_prompt(self.config, ctx.now(), self.store.list_preferences())

    def reply(self, session_id: str, message: str, image: Optional[dict] = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def load(self, session_id: str) -> list:
        return self.store.load_history(session_id, self.name)

    def save(self, session_id: str, history: list) -> None:
        trimmed = trim_history(history, self.config.max_history_items, self.is_turn_start)
        self.store.save_history(session_id, self.name, self.strip_images(trimmed))

    # Subclasses describe their own message format:
    def is_turn_start(self, message: Any) -> bool:  # pragma: no cover
        raise NotImplementedError

    def strip_images(self, history: list) -> list:  # pragma: no cover
        return history


def trim_history(history: list, max_items: int, is_turn_start: Callable[[Any], bool]) -> list:
    """Keep roughly the last ``max_items`` messages, cutting only at the start of a user turn so a
    tool call is never separated from its result (providers reject such histories)."""
    if len(history) <= max_items:
        return history
    for i in range(len(history) - max_items, len(history)):
        if is_turn_start(history[i]):
            return history[i:]
    return history[-1:] if is_turn_start(history[-1]) else []
