"""Common interfaces and errors for scoring providers."""

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
