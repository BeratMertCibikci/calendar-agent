"""Importing this package registers every tool with the global registry."""
from . import events, mail, schedule, tasks, web  # noqa: F401
