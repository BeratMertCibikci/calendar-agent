"""Process-wide wiring: the objects tools need (config, store, calendar, search).

Tools are plain functions, so they reach their dependencies through this holder.
``init_context`` is called once at startup; tests call it with fakes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from .config import Config
from .db import Store


@dataclass
class Context:
    config: Config
    store: Store
    calendar: Any  # a CalendarBackend, see calendar_backends.py
    search: Optional[Callable[[str], list[dict]]] = None  # web search, injectable for tests
    clock: Optional[Callable[[], datetime]] = None

    def now(self) -> datetime:
        return self.clock() if self.clock else datetime.now(self.config.tz)


_ctx: Optional[Context] = None


def init_context(ctx: Context) -> Context:
    global _ctx
    _ctx = ctx
    return ctx


def get_context() -> Context:
    if _ctx is None:
        raise RuntimeError("Context is not initialised; call init_context() (create_app() does this).")
    return _ctx


def reset_context() -> None:
    global _ctx
    _ctx = None
