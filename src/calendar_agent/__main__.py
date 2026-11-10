"""Command line: ``calendar-agent`` (serve) and ``calendar-agent auth`` (one-time Google sign-in)."""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from dotenv import load_dotenv

from .config import Config, ConfigError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calendar-agent", description="LLM calendar agent")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="start the web UI (default)")
    serve.add_argument("--engine", choices=["gemini", "ollama", "demo"], help="override ENGINE")
    serve.add_argument("--calendar", choices=["google", "memory"], help="override CALENDAR_BACKEND")
    serve.add_argument("--mail", choices=["none", "gmail", "memory"], help="override MAIL_BACKEND")
    serve.add_argument("--host", help="override HOST (default 127.0.0.1)")
    serve.add_argument("--port", type=int, help="override PORT (default 5001)")
    sub.add_parser("auth", help="sign in to Google Calendar once (opens a browser)")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "auth":
        from .google_auth import load_credentials, required_scopes
        scopes = required_scopes(config)
        if not scopes:
            print("Nothing to authorise: CALENDAR_BACKEND is not 'google' and MAIL_BACKEND is not 'gmail'.")
            return 0
        from .errors import ToolError
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
        "mail": getattr(args, "mail", None),
        "host": getattr(args, "host", None), "port": getattr(args, "port", None),
    }.items() if v is not None}
    config = dataclasses.replace(config, **overrides)

    from .server import create_app
    app = create_app(config)
    if config.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening on a non-local address. There is no login: anyone who can reach this port "
              "can use your calendar.", file=sys.stderr)
    print(f"Open http://{config.host}:{config.port}  (engine={config.engine}, calendar={config.calendar}, mail={config.mail})")
    app.run(host=config.host, port=config.port, debug=config.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
