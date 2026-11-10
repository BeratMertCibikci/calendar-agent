"""Ollama engine (local model).

Unlike Gemini's SDK, Ollama does not run the tool loop for us, so it lives here:
send messages + tool schemas -> if the model asks for tools, run them through the registry and
send the results back -> repeat until it answers in plain text (bounded by ``max_tool_rounds``).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from ..errors import EngineUnavailable
from .base import IMAGE_PLACEHOLDER, BaseEngine

log = logging.getLogger("calendar_agent.engine.ollama")


def _as_dict(message: Any) -> dict:
    """Ollama returns pydantic objects; history must be plain JSON-able dicts. model_dump() converts
    nested tool_calls too (dict(message) only converted the top level). None fields are dropped."""
    data = message.model_dump() if hasattr(message, "model_dump") else dict(message)
    return {k: v for k, v in data.items() if v is not None}


class OllamaEngine(BaseEngine):
    name = "ollama"

    def __init__(self, config, store, registry, chat_fn: Optional[Callable[..., Any]] = None):
        super().__init__(config, store, registry)
        self._chat = chat_fn

    @property
    def chat(self) -> Callable[..., Any]:
        if self._chat is None:
            try:
                import ollama
            except ImportError:
                raise EngineUnavailable("Ollama SDK missing. Install it: pip install 'calendar-agent[ollama]'") from None
            self._chat = ollama.chat
        return self._chat

    def reply(self, session_id: str, message: str, image: Optional[dict] = None) -> str:
        history = self.load(session_id)
        user_message: dict = {"role": "user", "content": message or "(photo)"}
        if image:
            user_message["images"] = [image["data"]]
        history.append(user_message)

        schemas = self.registry.json_schemas()
        for _ in range(self.config.max_tool_rounds):
            messages = [{"role": "system", "content": self.system_prompt()}] + history
            try:
                response = self.chat(model=self.config.ollama_model, messages=messages, tools=schemas)
            except ConnectionError:
                raise EngineUnavailable("Cannot reach Ollama. Is the Ollama app running? (try: ollama serve)") from None
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ == "ResponseError":
                    raise EngineUnavailable(f"Ollama error: {exc}") from None
                raise
            assistant = _as_dict(response["message"])
            history.append(assistant)

            calls = assistant.get("tool_calls") or []
            if not calls:
                self.save(session_id, history)
                return assistant.get("content") or "Done."

            for call in calls:
                function = call["function"]
                result = self.registry.call(function["name"], function.get("arguments") or {},
                                            session_id=session_id)
                log.info("%s -> %s", function["name"], result[:120].replace("\n", " "))
                history.append({"role": "tool", "name": function["name"], "content": result})

        self.save(session_id, history)
        return "I tried several steps without finishing. Could you simplify the request?"

    # -- history format -----------------------------------------------------------------------
    def is_turn_start(self, message: dict) -> bool:
        return message.get("role") == "user"

    def strip_images(self, history: list) -> list:
        out = []
        for message in history:
            if "images" in message:
                message = {k: v for k, v in message.items() if k != "images"}
                message["content"] = f"{message.get('content', '')} {IMAGE_PLACEHOLDER}".strip()
            out.append(message)
        return out
