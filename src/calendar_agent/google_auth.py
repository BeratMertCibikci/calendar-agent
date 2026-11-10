"""One Google sign-in shared by Calendar and Gmail.

Scopes depend on what is enabled, and they live in a single ``token.json``. If a feature is switched on
later (say Gmail), the old token lacks its scope: at runtime that produces an instruction to re-run
``calendar-agent auth`` instead of a confusing 403 from Google, and the interactive flow asks again.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .config import Config
from .errors import ToolError

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# Google has no "drafts only" scope. gmail.compose also allows sending; this program never calls send
# (the mail backend has no such method), which is covered by a test.
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"


def required_scopes(config: Config) -> list[str]:
    scopes: list[str] = []
    if config.calendar == "google":
        scopes.append(CALENDAR_SCOPE)
    if config.mail == "gmail":
        scopes += [GMAIL_READ_SCOPE, GMAIL_COMPOSE_SCOPE]
    return scopes


def granted_scopes(token_file) -> set[str]:
    """Scopes recorded in token.json (empty if the file is missing or unreadable)."""
    try:
        with open(token_file, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return set()
    raw = info.get("scopes") or info.get("scope") or []
    return set(raw.split() if isinstance(raw, str) else raw)


def http_status(exc: Exception) -> Optional[int]:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return int(status) if status is not None else None


def save_token(config: Config, creds) -> None:
    path = config.token_file
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only: it is a credential
    with os.fdopen(fd, "w") as handle:
        handle.write(creds.to_json())


def load_credentials(config: Config, interactive: bool = False):
    scopes = required_scopes(config)
    token_file, secrets_file = config.token_file, config.credentials_file
    have_token = token_file.exists()
    missing = [s for s in scopes if s not in granted_scopes(token_file)] if have_token else scopes
    if have_token and missing and not interactive:
        raise ToolError("Google permissions changed (a new feature was enabled). Ask the user to run "
                        "`calendar-agent auth` in a terminal once more.")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise ToolError("Google libraries are missing. Install them: pip install 'calendar-agent[google]'") from None

    creds = Credentials.from_authorized_user_file(str(token_file), scopes) if have_token and not missing else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(config, creds)
    elif not creds or not creds.valid:
        if not interactive:
            raise ToolError("Google is not authorised yet. Ask the user to run `calendar-agent auth` in a "
                            "terminal once.")
        if not secrets_file.exists():
            raise ToolError(f"Missing {secrets_file}. Download the OAuth client JSON from Google Cloud Console "
                            "(see README) and save it there.")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), scopes)
        creds = flow.run_local_server(port=0)
        save_token(config, creds)
    return creds


def build_service(config: Config, api: str, version: str, interactive: bool = False):
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise ToolError("Google libraries are missing. Install them: pip install 'calendar-agent[google]'") from None
    creds = load_credentials(config, interactive)
    return build(api, version, credentials=creds, cache_discovery=False)
