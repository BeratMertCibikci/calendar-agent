"""Exception types shared across the package."""


class ToolError(Exception):
    """A tool failed for a reason the model (and user) should hear about.

    The message is returned to the LLM as the tool result, so write it as an
    instruction the model can act on ("start must be before end").
    """


class EventNotFound(ToolError):
    def __init__(self, event_id: str):
        super().__init__(f"Event not found: {event_id}. Use list_events to find a valid id.")


class EngineUnavailable(Exception):
    """The LLM backend could not answer (overloaded, unreachable, all models failed)."""
