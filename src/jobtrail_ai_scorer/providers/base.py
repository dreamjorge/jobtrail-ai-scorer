"""Common interfaces and errors for scoring providers."""

import math
from typing import Protocol


class ProviderError(RuntimeError):
    """Base error raised by a scoring provider."""


class ProviderConfigurationError(ProviderError):
    """Raised when a provider lacks required runtime configuration."""


class ProviderProcessError(ProviderError):
    """Raised when a local provider process cannot produce a score."""


class ScoreProvider(Protocol):
    """A provider that returns the model's unparsed response text."""

    def score(self, prompt: str) -> str:
        """Return raw model text for ``prompt``."""


def validate_timeout(timeout_seconds: float) -> None:
    """Reject timeouts that subprocess and HTTP clients cannot safely use."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
