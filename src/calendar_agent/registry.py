"""Tool registry: the single place where model-callable functions are declared.

A tool is a plain, type-hinted Python function with a Google-style docstring.
From that one definition the registry derives
  * the JSON schema sent to engines that need one (Ollama / OpenAI style),
  * a callable for engines whose SDK introspects functions itself (Gemini),
  * argument normalisation, so sloppy model output (None for unused optionals,
    "5" for an int, "true" for a bool) never reaches the tool body,
  * the approval gate: tools flagged ``requires_approval`` are *not* executed
    when the model calls them. The call is parked in the database and a human
    has to approve it from the UI. The model has no parameter it could set to
    skip that step, which is the whole point.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .context import get_context
from .errors import ToolError

log = logging.getLogger("calendar_agent.tools")

_session_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("session_id", default=None)

_TYPE_NAMES = {str: "string", int: "integer", float: "number", bool: "boolean"}
_TRUE = {"true", "yes", "1", "y", "oui", "evet"}
_FALSE = {"false", "no", "0", "n", "non", "hayır", "hayir"}
_ARG_RE = re.compile(r"^(\w+)\s*(\(.*?\))?:\s*(.*)$")


@contextlib.contextmanager
def session_scope(session_id: Optional[str]):
    """Make ``session_id`` visible to tools called inside the block (e.g. by an SDK
    that executes them on its own)."""
    token = _session_var.set(session_id)
    try:
        yield
    finally:
        _session_var.reset(token)


def current_session() -> Optional[str]:
    return _session_var.get()


def parse_docstring(doc: Optional[str]) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (description, {param: description})."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip() in ("Args:", "Arguments:")), None)
    if idx is None:
        return " ".join(line.strip() for line in lines if line.strip()), {}
    description = " ".join(line.strip() for line in lines[:idx] if line.strip())
    params: dict[str, str] = {}
    current: Optional[str] = None
    base: Optional[int] = None
    for line in lines[idx + 1:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            base = indent
        if indent < base:
            break
        match = _ARG_RE.match(line.strip()) if indent == base else None
        if match:
            current = match.group(1)
            params[current] = match.group(3).strip()
        elif current:
            params[current] = f"{params[current]} {line.strip()}".strip()
    return description, params


def _coerce(value: Any, py_type: type) -> Any:
    """Convert ``value`` to ``py_type`` or raise ValueError."""
    if py_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE:
                return True
            if text in _FALSE:
                return False
        raise ValueError("expected a boolean")
    if py_type is int:
        if isinstance(value, bool):
            raise ValueError("expected an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            return int(value.strip())
        raise ValueError("expected an integer")
    if py_type is float:
        if isinstance(value, bool):
            raise ValueError("expected a number")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value.strip())
        raise ValueError("expected a number")
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ValueError("expected a string")


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str
    properties: dict[str, dict]
    required: list[str]
    types: dict[str, type]
    defaults: dict[str, Any]
    requires_approval: bool = False
    preview: Optional[Callable[..., str]] = None
    param_order: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------ declaration
    def register(self, fn: Callable[..., Any], *, requires_approval: bool = False,
                 preview: Optional[Callable[..., str]] = None) -> ToolSpec:
        description, param_docs = parse_docstring(fn.__doc__)
        if not description:
            raise TypeError(f"Tool {fn.__name__} needs a docstring: the model reads it to decide when to call the tool.")
        sig = inspect.signature(fn)
        properties: dict[str, dict] = {}
        required: list[str] = []
        types: dict[str, type] = {}
        defaults: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
            if annotation not in _TYPE_NAMES:
                raise TypeError(f"Tool {fn.__name__}: parameter {name!r} has unsupported type {annotation!r} "
                                f"(allowed: {', '.join(t.__name__ for t in _TYPE_NAMES)})")
            types[name] = annotation
            properties[name] = {"type": _TYPE_NAMES[annotation]}
            if name in param_docs:
                properties[name]["description"] = param_docs[name]
            if param.default is inspect.Parameter.empty:
                required.append(name)
            else:
                defaults[name] = param.default
        if requires_approval and preview is None:
            raise TypeError(f"Tool {fn.__name__}: requires_approval needs a preview() so the user sees what they approve.")
        spec = ToolSpec(fn.__name__, fn, description, properties, required, types, defaults,
                        requires_approval, preview, list(sig.parameters))
        self._tools[spec.name] = spec
        return spec

    # ------------------------------------------------------------- inspection
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def json_schemas(self) -> list[dict]:
        """OpenAI/Ollama-style function declarations, generated from the Python signatures."""
        return [
            {"type": "function", "function": {
                "name": s.name,
                "description": s.description,
                "parameters": {"type": "object", "properties": s.properties, "required": s.required},
            }}
            for s in self._tools.values()
        ]

    def callables(self) -> list[Callable[..., str]]:
        """Callables for SDKs that introspect Python functions (Gemini). Each one routes through
        :meth:`call`, so normalisation, error handling and the approval gate still apply."""
        return [self._make_callable(spec) for spec in self._tools.values()]

    def _make_callable(self, spec: ToolSpec) -> Callable[..., str]:
        @functools.wraps(spec.fn)
        def _call(**kwargs: Any) -> str:
            return self.call(spec.name, kwargs)
        return _call

    # --------------------------------------------------------------- dispatch
    def _normalise(self, spec: ToolSpec, arguments: dict) -> dict:
        unknown = sorted(set(arguments) - set(spec.param_order))
        if unknown:
            raise ToolError(f"Unknown argument(s) for {spec.name}: {', '.join(unknown)}. "
                            f"Valid arguments: {', '.join(spec.param_order) or 'none'}.")
        clean: dict[str, Any] = {}
        for name, value in arguments.items():
            if value is None:
                continue  # small models send null for "not used"; treat as absent
            try:
                clean[name] = _coerce(value, spec.types[name])
            except (ValueError, TypeError):
                raise ToolError(f"Argument {name!r} of {spec.name} must be {_TYPE_NAMES[spec.types[name]]}, "
                                f"got {value!r}.") from None
        missing = [n for n in spec.required if n not in clean]
        if missing:
            raise ToolError(f"Missing required argument(s) for {spec.name}: {', '.join(missing)}.")
        return clean

    def call(self, name: str, arguments: Any, *, approved: bool = False,
             session_id: Optional[str] = None) -> str:
        """Run a tool and always return text for the model. Never raises."""
        spec = self._tools.get(name)
        if spec is None:
            return f"Error: unknown tool {name!r}. Available tools: {', '.join(self._tools)}."
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments or "{}")
            if not isinstance(arguments, dict):
                raise ToolError("Tool arguments must be an object.")
            args = self._normalise(spec, arguments)

            if spec.requires_approval and not approved:
                return self._park(spec, args, session_id or current_session())

            log.info("tool %s %s", name, _short(args))
            return str(spec.fn(**args))
        except ToolError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool bug must not crash the conversation
            log.exception("tool %s crashed", name)
            return f"Error: {type(exc).__name__}: {str(exc)[:300]}"

    def _park(self, spec: ToolSpec, args: dict, session_id: Optional[str]) -> str:
        if not session_id:
            raise ToolError("This action needs user approval but no session is active.")
        summary = spec.preview(**args)  # may raise ToolError, e.g. "event not found"
        action_id = get_context().store.create_action(session_id, spec.name, args, summary)
        log.info("tool %s parked as action #%s: %s", spec.name, action_id, summary)
        return (f"APPROVAL REQUIRED: action #{action_id} ({summary}) has NOT been executed. "
                "The user now sees Approve / Reject buttons in the app. Tell them what is waiting for "
                "approval, then stop. Do not call this tool again for the same request.")


def _short(args: dict, limit: int = 200) -> str:
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


registry = ToolRegistry()


def tool(fn: Optional[Callable[..., Any]] = None, *, requires_approval: bool = False,
         preview: Optional[Callable[..., str]] = None):
    """Decorator that registers a function as a model-callable tool and returns it unchanged."""
    def decorate(f: Callable[..., Any]) -> Callable[..., Any]:
        registry.register(f, requires_approval=requires_approval, preview=preview)
        return f
    return decorate(fn) if fn is not None else decorate
