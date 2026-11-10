"""LLM engines. Each one turns (session, message, optional image) into a reply, using the tool registry."""
from ..config import Config
from ..db import Store
from ..registry import ToolRegistry
from .base import BaseEngine


def build_engine(config: Config, store: Store, registry: ToolRegistry) -> BaseEngine:
    if config.engine == "gemini":
        from .gemini import GeminiEngine
        return GeminiEngine(config, store, registry)
    if config.engine == "ollama":
        from .ollama import OllamaEngine
        return OllamaEngine(config, store, registry)
    from .demo import DemoEngine
    return DemoEngine(config, store, registry)


__all__ = ["BaseEngine", "build_engine"]
