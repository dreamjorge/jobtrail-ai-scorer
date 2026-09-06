"""Fake WhatsApp helper used by the hermetic e2e suite.

The production orchestrator sends the daily summary to a WhatsApp helper by
calling a callable that receives a single ``str`` argument (the rendered
message). This stub captures every call into an in-memory buffer so tests
can assert on the exact bytes the helper would have received.

The stub never invokes an external service and never touches the
filesystem; the captured messages are plain Python strings.
"""

from __future__ import annotations


class StubWhatsApp:
    """Record every message the orchestrator hands to the WhatsApp helper."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        """Append ``message`` to the in-memory buffer."""

        self.messages.append(str(message))

    @property
    def bodies(self) -> list[str]:
        """Alias kept for symmetry with the captured ``messages`` list."""

        return self.messages

    def reset(self) -> None:
        """Clear the buffer (useful when the same stub is reused)."""

        self.messages.clear()
