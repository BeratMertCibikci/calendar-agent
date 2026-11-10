# Design notes

Decisions and bugs from building this, written as short stories.

## 1. Approval must live in code, not in the prompt
First version: the system prompt said "ask before deleting". Models skip such rules. Now `delete_event` and `import_ics` are registered with `requires_approval=True`: the registry stores the call in `pending_actions` and returns text for the model. Only the HTTP endpoint triggered by the user's click executes it. The claim is an atomic `UPDATE ... WHERE status='pending'`, so a double click or replay cannot run it twice; a different session cannot approve it; it expires after 30 minutes.

## 2. Local models send bad arguments
Ollama passed `reminder_minutes: None` and the tool crashed on `None > 0`. The registry now normalises arguments from the function signature (None → default, `"5"` → 5, `"true"` → True) and returns unknown/missing-argument errors **as text**, so the model can correct itself instead of the request failing.

## 3. Ollama history serialisation
`dict(message)` converted only the top level, leaving `tool_calls` as objects, which then got stringified and broke the next call. Fix: `model_dump()` plus dropping `None` fields.

## 4. History trimming can corrupt a conversation
Cutting history at an arbitrary index can separate a tool call from its result, which providers reject. `trim_history` only cuts at the start of a user turn (a user message that is not a tool result). Stored images are replaced by a placeholder so they are not re-sent on every turn.

## 5. Gemini availability
Models were retired (404) and busy (503) at different times. The engine tries a configurable model list; 404 skips to the next model, 429/5xx retry with backoff then move on, 400/401/403 are raised immediately because retrying cannot help.

## 6. OAuth inside a request
The prototype started the OAuth browser flow inside a chat request, which hangs. Authorisation is now a separate command (`calendar-agent auth`); at runtime a missing token produces a clear message for the model/user.

## 7. Idempotent ICS import
Timetables get re-exported. Each imported event carries `source=ics` and `ics_uid` in private extended properties; import looks them up and creates, updates or skips. The test suite imports the same file twice and then a modified one.

## 8. Wrong match time ("PSG at 15:00")
The model reported a UTC time as local. Two fixes: a prompt rule to convert UTC to the local zone, and a rule to say when a time comes from a source instead of guessing. Lesson: log what the tool returned before blaming the model.

## Threat model summary
See README. Principle: treat the model, web results and imported files as untrusted input; keep authority (approval, file access, credentials) in deterministic code.
