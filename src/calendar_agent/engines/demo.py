"""Demo engine: keyword rules instead of an LLM.

It exists so anyone can clone the repo and try the UI, the tools and the approval flow without an API key,
and so the whole stack (server -> engine -> registry -> calendar) can be tested end to end in CI.
It is deliberately dumb: a handful of commands, English only.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from ..context import get_context
from .base import BaseEngine

HELP = """I am the demo engine: no AI, just keyword rules. Try:
- add Team meeting on 2026-11-12 14:00 for 90 min
- events  (next 7 days)
- free 2026-11-12  (free slots that day)
- delete evt1  (asks for approval)
- task Buy train ticket / tasks / done 1
- remember Meetings last 90 minutes
- search PSG Le Mans kick-off
- mail [gmail query]  (needs MAIL_BACKEND=memory or gmail) / read m1
- reply m1 | Thursday 15:00 works for me.  (makes a draft, asks approval)
- draft a@b.com | Subject | Body text"""

_ADD = re.compile(r"^add\s+(?P<title>.+?)\s+on\s+(?P<day>\d{4}-\d{2}-\d{2})\s+(?P<hm>\d{1,2}:\d{2})"
                  r"(?:\s+for\s+(?P<mins>\d+)\s*(?:min|minutes)?)?$", re.IGNORECASE)


class DemoEngine(BaseEngine):
    name = "demo"

    def reply(self, session_id: str, message: str, image: Optional[dict] = None) -> str:
        raw = message or ""
        # The server may prepend a notice about an approval; the user's own words come after the marker.
        if raw.startswith("[System notice") and "User message:\n" in raw:
            raw = raw.split("User message:\n", 1)[1]
        text = " ".join(raw.split())
        def call(name, **kw):
            result = self.registry.call(name, kw, session_id=session_id)
            if result.startswith("APPROVAL REQUIRED"):  # the long text is written for an LLM, not a person
                return "This needs your approval. Use the buttons below."
            return result

        ctx = get_context()
        lower = text.lower()

        if image:
            return "The demo engine cannot read photos. Use ENGINE=gemini for that."
        if lower in ("", "help", "?"):
            return HELP
        if m := _ADD.match(text):
            hour, minute = m["hm"].split(":")
            start = f"{m['day']}T{int(hour):02d}:{minute}:00"
            from ..timeutil import parse_dt
            end = (parse_dt(start, ctx.config.tz) + timedelta(minutes=int(m["mins"] or 60))).isoformat()
            return call("create_event", title=m["title"], start=start, end=end)
        if lower.startswith("events"):
            now = ctx.now()
            return call("list_events", start=now.isoformat(), end=(now + timedelta(days=7)).isoformat())
        if lower.startswith("free"):
            arg = text[4:].strip() or ctx.now().date().isoformat()
            return call("find_free_slots", start_date=arg, end_date=arg)
        if lower.startswith("delete "):
            return call("delete_event", event_id=text[7:].strip())
        if lower.startswith("import "):
            return call("import_ics", file_path=text[7:].strip())
        if lower.startswith("task "):
            return call("add_task", text=text[5:])
        if lower == "tasks":
            return call("list_tasks")
        if lower.startswith("done "):
            return call("complete_task", task_id=text[5:].strip())
        if lower.startswith("remember "):
            return call("remember", fact=text[9:])
        if lower == "mail" or lower.startswith("mail "):
            return call("search_mail", query=text[5:])
        if lower.startswith("read "):
            return call("read_mail", message_id=text[5:].strip())
        if lower.startswith("reply "):
            ident, sep, body = text[6:].partition("|")
            return call("draft_email", reply_to_message_id=ident.strip(), body=body.strip()) if sep else \
                "Usage: reply <id> | <your text>"
        if lower.startswith("draft "):
            parts = [p.strip() for p in text[6:].split("|", 2)]
            return call("draft_email", to=parts[0], subject=parts[1], body=parts[2]) if len(parts) == 3 else \
                "Usage: draft <to> | <subject> | <text>"
        if lower.startswith("search "):
            return call("web_search", query=text[7:])
        return "I did not understand that. Type 'help' for the commands I know."

    def is_turn_start(self, message) -> bool:
        return True
