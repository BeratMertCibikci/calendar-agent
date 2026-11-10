"""Command line.

  calendar-agent          start the web UI in your browser (same as ``serve``)
  calendar-agent app      open it in its own desktop window (needs the ``app`` extra)
  calendar-agent auth     one-time Google sign-in (Calendar and/or Gmail, whichever is enabled)
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import Config, ConfigError


def _add_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", choices=["gemini", "ollama", "demo"], help="override ENGINE")
    parser.add_argument("--calendar", choices=["google", "memory"], help="override CALENDAR_BACKEND")
    parser.add_argument("--mail", choices=["none", "gmail", "memory"], help="override MAIL_BACKEND")
    parser.add_argument("--host", help="override HOST (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="override PORT (default 5001)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calendar-agent", description="LLM calendar agent")
    sub = parser.add_subparsers(dest="command")
    _add_overrides(sub.add_parser("serve", help="start the web UI in the browser (default)"))
    _add_overrides(sub.add_parser("app", help="open the UI in a desktop window"))
    sub.add_parser("auth", help="sign in to Google once (opens a browser)")
    return parser


def _load_environment(command) -> None:
    load_dotenv()  # .env in the working directory, if any
    if command == "app":
        # A packaged app has no useful working directory: keep data and settings in the user's app folder.
        from .desktop import default_data_dir
        os.environ.setdefault("DATA_DIR", str(default_data_dir()))
        load_dotenv(Path(os.environ["DATA_DIR"]) / ".env")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    _load_environment(args.command)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "auth":
        from .errors import ToolError
        from .google_auth import load_credentials, required_scopes
        scopes = required_scopes(config)
        if not scopes:
            print("Nothing to authorise: CALENDAR_BACKEND is not 'google' and MAIL_BACKEND is not 'gmail'.")
            return 0
        try:
            load_credentials(config, interactive=True)
        except ToolError as exc:
            print(f"Sign-in failed: {exc}", file=sys.stderr)
            return 1
        print("Signed in with access to: " + ", ".join(s.rsplit("/", 1)[-1] for s in scopes))
        print(f"Token saved to {config.token_file}")
        return 0

    overrides = {k: v for k, v in {
        "engine": getattr(args, "engine", None), "calendar": getattr(args, "calendar", None),
        "mail": getattr(args, "mail", None), "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
    }.items() if v is not None}
    config = dataclasses.replace(config, **overrides)

    if args.command == "app":
        from .desktop import run_app
        from .errors import ToolError
        try:
            return run_app(config)
        except ToolError as exc:
            print(exc, file=sys.stderr)
            return 1

    from .server import create_app
    app = create_app(config)
    if config.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening on a non-local address. There is no login: anyone who can reach this port "
              "can use your calendar.", file=sys.stderr)
    print(f"Open http://{config.host}:{config.port}  (engine={config.engine}, calendar={config.calendar}, "
          f"mail={config.mail})")
    app.run(host=config.host, port=config.port, debug=config.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
