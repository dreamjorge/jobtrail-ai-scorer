"""Hermes command-line scoring provider."""

from dataclasses import dataclass
import subprocess

from .base import ProviderProcessError, validate_timeout


@dataclass(frozen=True)
class HermesProviderConfig:
    """Runtime configuration for a Hermes executable."""

    executable: str
    profile: str
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        validate_timeout(self.timeout_seconds)
        if self.profile.startswith("-"):
            raise ValueError("profile must not start with a dash")


class HermesProvider:
    """Invoke a configured Hermes executable without a shell."""

    def __init__(self, config: HermesProviderConfig) -> None:
        self._config = config

    def score(self, prompt: str) -> str:
        """Return Hermes' raw standard output for ``prompt``."""
        command = [self._config.executable, "--profile", self._config.profile, "-z", prompt, "--cli"]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=self._config.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise ProviderProcessError("Hermes process timed out") from None
        except OSError as error:
            raise ProviderProcessError("Unable to start Hermes process") from error

        if result.returncode != 0:
            raise ProviderProcessError(
                f"Hermes process failed with exit code {result.returncode}"
            )
        return result.stdout
