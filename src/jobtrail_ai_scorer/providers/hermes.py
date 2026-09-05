"""Hermes command-line scoring provider."""

from dataclasses import dataclass
import subprocess

from .base import ProviderProcessError


@dataclass(frozen=True)
class HermesProviderConfig:
    """Runtime configuration for a Hermes executable."""

    executable: str
    profile: str
    timeout_seconds: float = 60.0


class HermesProvider:
    """Invoke a configured Hermes executable without a shell."""

    def __init__(self, config: HermesProviderConfig) -> None:
        self._config = config

    def score(self, prompt: str) -> str:
        """Return Hermes' raw standard output for ``prompt``."""
        command = [self._config.executable, "--profile", self._config.profile]
        try:
            result = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                check=False,
                text=True,
                timeout=self._config.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ProviderProcessError("Hermes process timed out") from error
        except OSError as error:
            raise ProviderProcessError(f"Unable to start Hermes process: {error}") from error

        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise ProviderProcessError(f"Hermes process failed: {detail}")
        return result.stdout
