# Calendar Agent

An LLM agent that manages your Google Calendar from a chat window. You write *"Friday 14:00 ML meeting, remind me 30 min before"*; the model calls typed tools (create, list, update, delete, find free slots, import a timetable), and **anything destructive waits for your click**.

It runs with a cloud model (Gemini) or a fully local one (Ollama), and has a keyword-only demo engine so you can try everything without an API key or a Google account.

![screenshot](docs/screenshot.png)
<sub>Screenshot: demo engine (scripted commands, no LLM) with the in-memory calendar.</sub>

## Features

- **Tool calling** with schemas generated from type hints and docstrings. Argument normalisation (`null`, `"5"`, `"true"`) because local models send sloppy arguments.
- **Human-in-the-loop approval enforced in code.** Tools flagged `requires_approval` are parked in SQLite; the model only sees "waiting for approval". The UI's Approve button executes them. The claim is atomic (no double execution), actions expire after 30 min, and the outcome is fed back to the model on the next message.
- **Conflict detection** before creating events (adjacent events are fine, all-day/"free" events are ignored) and `find_free_slots`.
- **Idempotent timetable import** (`.ics`, e.g. a university ADE export): a UID is stored in the event's private extended properties, so re-importing updates changed lessons instead of duplicating them.
- **Photo → event** (Gemini): send a ticket screenshot, it reads date and time.
- **Persistent memory** of user preferences (bounded: 50 × 300 chars) and tasks.
- **Two engines + fallbacks**: Gemini with model fallback list and retry/backoff on 429/5xx; Ollama with a bounded manual tool loop.
- Web search whose results are marked as untrusted data (prompt-injection mitigation).
- UI: single page, no build step, light/dark, EN/TR/FR, approval cards, session restore, client-side image downscaling.

## Architecture

```mermaid
flowchart LR
  UI[Browser UI] -->|JSON + CSRF token| S[Flask server]
  S --> E{Engine}
  E -->|Gemini SDK| G[(Gemini)]
  E -->|ollama.chat| O[(Local model)]
  E --> R[Tool registry]
  R -->|safe tools| C[CalendarBackend]
  R -->|requires_approval| P[(pending_actions)]
  UI -->|Approve / Reject| S
  S -->|executes approved| R
  C --> GC[Google Calendar]
  C --> M[In-memory calendar]
  R --> DB[(SQLite: tasks, memory, history)]
```

The model never talks to Google directly: every capability is a function in `tools/`, registered with `@tool`. Calendar access sits behind a small `CalendarBackend` protocol, so tests and demo mode use an in-memory implementation.

## Quick start (no keys needed)

```bash
git clone <this repo> && cd calendar-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ENGINE=demo CALENDAR_BACKEND=memory calendar-agent
# open http://127.0.0.1:5001 and type: help
```

## Use it for real

```bash
pip install -e ".[all]"
cp .env.example .env        # fill in GEMINI_API_KEY (or set ENGINE=ollama)
```

**Google Calendar** (one-time): in Google Cloud Console enable *Google Calendar API*, create an *OAuth client ID (Desktop app)*, add your account under *Audience → Test users*, download the JSON to `data/credentials.json`, then:

```bash
calendar-agent auth     # opens a browser once, saves data/token.json (chmod 600)
calendar-agent          # starts the UI on http://127.0.0.1:5001
```

**Local model**: install [Ollama](https://ollama.com), `ollama pull qwen2.5:14b` (any tool-capable model), set `ENGINE=ollama` and `OLLAMA_MODEL`.

Configuration is via environment variables / `.env`; see [.env.example](.env.example).

## Security model

It is a local app that can modify your calendar, so it is built defensively:

| Threat | Mitigation |
|---|---|
| Model (or injected web text) deletes/imports things | Approval gate in code, not in the prompt |
| Prompt injection via search results / ICS content | Results labelled as untrusted data; destructive tools need a human click |
| Malicious website calls `localhost` (CSRF, DNS rebinding) | Host allow-list, Origin check, per-process CSRF token, JSON-only bodies |
| Model reads arbitrary files via `import_ics` | Path allow-list (`ICS_ALLOWED_DIRS`), `.ics` only, ≤ 5 MB, ≤ 500 events |
| Memory poisoning | Preference count/length limits, preferences shown to the model as data |
| Credential leakage | `token.json` created `0600`; `.env`, `data/`, tokens git-ignored |
| Exposed by accident | Binds `127.0.0.1`, debug off by default, warning when bound elsewhere |

There is no login: do not expose the port to a network.

## Tests

```bash
pytest          # 67 tests, no network, no API keys
```

Engines are tested through seams (`_attempt` for Gemini, `chat_fn` for Ollama, a fake service for Google), so retry/fallback logic, history trimming and the approval flow are covered without the SDKs.

## Layout

```
src/calendar_agent/
  registry.py          tool decorator, schema generation, approval gate
  tools/               events, schedule (ICS import), tasks + memory, web
  engines/             gemini, ollama, demo
  calendar_backends.py Google + in-memory
  icsparse.py          small .ics reader
  db.py                SQLite store (history, tasks, memory, pending actions)
  server.py            Flask app + security checks
  static/index.html    UI
tests/  docs/DESIGN_NOTES.md
```

## Limitations / roadmap

- Single user, single calendar (`primary`). No recurring-event expansion for conflict checks.
- The Gemini, Ollama and Google paths are covered by fakes in CI; they need a manual run against the real services before relying on them.
- Ideas: streaming replies, Apple Calendar (CalDAV) backend, multiple calendars, voice input.

## License

MIT
