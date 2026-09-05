"""Minimal boundary for OpenAI-compatible chat completion APIs."""

from dataclasses import dataclass
import os
from typing import Any, Callable

import httpx

from .base import ProviderConfigurationError, ProviderError, validate_timeout

Transport = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    """Explicit settings required by an OpenAI-compatible endpoint."""

    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        validate_timeout(self.timeout_seconds)


class OpenAICompatibleProvider:
    """Request raw completions from a configured OpenAI-compatible API."""

    def __init__(
        self, config: OpenAICompatibleConfig, *, transport: Transport | None = None
    ) -> None:
        self._config = config
        self._transport = transport or self._post

    def score(self, prompt: str) -> str:
        """Return the first completion's raw text."""
        api_key = os.environ.get(self._config.api_key_env)
        if not api_key:
            raise ProviderConfigurationError(
                f"Required API-key environment variable is unset: {self._config.api_key_env}"
            )

        try:
            response = self._transport(
                self._config.endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content:
                raise TypeError("completion content must be a non-empty string")
            return content
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("OpenAI-compatible provider returned an invalid response") from error

    def _post(self, endpoint: str, *, headers: dict[str, str], json: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            endpoint,
            headers=headers,
            json=json,
            timeout=self._config.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
