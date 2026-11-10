"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENGINES = ("gemini", "ollama", "demo")
CALENDARS = ("google", "memory")
MAIL_BACKENDS = ("none", "gmail", "memory")
LANGUAGES = {"tr": "Turkish", "en": "English", "fr": "French"}
# Tried in order. Model names are retired regularly, so the engine falls through the list.
DEFAULT_GEMINI_MODELS = ("gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash")


class ConfigError(ValueError):
    """An environment variable has a value we cannot use."""


def _int(env: Mapping[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}, got {value}")
    return value


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    engine: str = "gemini"
    calendar: str = "google"
    mail: str = "none"
    gemini_models: tuple = DEFAULT_GEMINI_MODELS
    ollama_model: str = "qwen2.5:14b"
    timezone: str = "Europe/Paris"
    language: str = "en"
    user_context: str = ""
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 5001
    debug: bool = False
    max_history_items: int = 40
    max_tool_rounds: int = 6
    action_ttl_seconds: int = 1800
    ics_allowed_dirs: tuple = ()

    # -- derived paths / values -------------------------------------------------
    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "agent.db"

    @property
    def credentials_file(self) -> Path:
        return Path(self.data_dir) / "credentials.json"

    @property
    def token_file(self) -> Path:
        return Path(self.data_dir) / "token.json"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def language_name(self) -> str:
        return LANGUAGES.get(self.language, "English")

    # -- construction ------------------------------------------------------------
    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if env is None else env

        engine = env.get("ENGINE", "gemini").strip().lower() or "gemini"
        if engine not in ENGINES:
            raise ConfigError(f"ENGINE must be one of {ENGINES}, got {engine!r}")

        calendar = env.get("CALENDAR_BACKEND", "google").strip().lower() or "google"
        if calendar not in CALENDARS:
            raise ConfigError(f"CALENDAR_BACKEND must be one of {CALENDARS}, got {calendar!r}")

        mail = env.get("MAIL_BACKEND", "none").strip().lower() or "none"
        if mail not in MAIL_BACKENDS:
            raise ConfigError(f"MAIL_BACKEND must be one of {MAIL_BACKENDS}, got {mail!r}")

        timezone = env.get("TIMEZONE", "Europe/Paris").strip() or "Europe/Paris"
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"TIMEZONE {timezone!r} is not a valid IANA zone (e.g. Europe/Paris)") from None

        language = env.get("ASSISTANT_LANGUAGE", "en").strip().lower() or "en"
        if language not in LANGUAGES:
            raise ConfigError(f"ASSISTANT_LANGUAGE must be one of {tuple(LANGUAGES)}, got {language!r}")

        models_raw = env.get("GEMINI_MODELS", "")
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip()) or DEFAULT_GEMINI_MODELS

        dirs_raw = env.get("ICS_ALLOWED_DIRS", "")
        if dirs_raw.strip():
            ics_dirs = tuple(Path(p).expanduser() for p in dirs_raw.split(os.pathsep) if p.strip())
        else:
            ics_dirs = (Path.home(), Path.cwd())

        return cls(
            engine=engine,
            calendar=calendar,
            mail=mail,
            gemini_models=models,
            ollama_model=env.get("OLLAMA_MODEL", "").strip() or "qwen2.5:14b",
            timezone=timezone,
            language=language,
            user_context=env.get("USER_CONTEXT", "").strip()[:500],
            data_dir=Path(env.get("DATA_DIR", "").strip() or "data"),
            host=env.get("HOST", "").strip() or "127.0.0.1",
            port=_int(env, "PORT", 5001, 1, 65535),
            debug=_bool(env, "DEBUG"),
            ics_allowed_dirs=ics_dirs,
        )
