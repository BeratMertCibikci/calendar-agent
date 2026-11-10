"""Entry point used by PyInstaller: opens the desktop window."""
from calendar_agent.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["app"]))
