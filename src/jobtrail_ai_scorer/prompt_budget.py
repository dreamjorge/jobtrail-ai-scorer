"""Bounded loading of optional candidate context for provider prompts."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Literal


DEFAULT_PROFILE_BUDGET_CHARS = 12_000
DEFAULT_CV_BUDGET_CHARS = 16_000
DEFAULT_TRUNCATE_MARKER = "\n[... content truncated ...]"
LoadStatus = Literal["loaded", "truncated", "missing", "unreadable"]


@dataclass(frozen=True)
class PromptBudget:
    """Character limits and the marker used when candidate context is clipped."""

    profile_budget_chars: int = DEFAULT_PROFILE_BUDGET_CHARS
    cv_budget_chars: int = DEFAULT_CV_BUDGET_CHARS
    marker: str = DEFAULT_TRUNCATE_MARKER

    def __post_init__(self) -> None:
        if (
            isinstance(self.profile_budget_chars, bool)
            or not isinstance(self.profile_budget_chars, int)
            or self.profile_budget_chars <= 0
        ):
            raise ValueError("profile_budget_chars budget must be positive")
        if (
            isinstance(self.cv_budget_chars, bool)
            or not isinstance(self.cv_budget_chars, int)
            or self.cv_budget_chars <= 0
        ):
            raise ValueError("cv_budget_chars budget must be positive")
        if not isinstance(self.marker, str) or not self.marker.strip():
            raise ValueError("marker must not be empty")

    @classmethod
    def from_env(cls) -> PromptBudget:
        """Build a prompt budget, applying optional environment overrides."""

        return cls(
            profile_budget_chars=_positive_int_from_env(
                "PROMPT_PROFILE_BUDGET", DEFAULT_PROFILE_BUDGET_CHARS
            ),
            cv_budget_chars=_positive_int_from_env(
                "PROMPT_CV_BUDGET", DEFAULT_CV_BUDGET_CHARS
            ),
            marker=os.environ.get("PROMPT_TRUNCATE_MARKER", DEFAULT_TRUNCATE_MARKER),
        )

    def clip_with_marker(self, text: str, budget: int) -> str:
        """Clip ``text`` to ``budget`` characters, appending this budget's marker."""

        return clip_with_marker(text, budget, self.marker)

    def clip(self, text: str, budget: int) -> str:
        """Alias for :meth:`clip_with_marker`."""

        return self.clip_with_marker(text, budget)

    def load_optional_text(
        self, path: Path | str, budget: int, marker: str | None = None
    ) -> tuple[str, LoadStatus]:
        """Load and clip a file, returning a status instead of raising file errors."""

        return load_optional_text(path, budget, self.marker if marker is None else marker)


def clip_with_marker(text: str, budget: int, marker: str) -> str:
    """Return ``text`` unchanged or a bounded prefix followed by ``marker``.

    The marker is part of the character budget. If a caller chooses a budget
    shorter than the marker itself, the marker is clipped to fit the budget.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("budget must be positive")
    if not isinstance(marker, str) or not marker:
        raise ValueError("marker must not be empty")
    if len(text) <= budget:
        return text
    if len(marker) >= budget:
        return marker[:budget]
    return text[: budget - len(marker)] + marker


def load_optional_text(
    path: Path | str, budget: int, marker: str
) -> tuple[str, LoadStatus]:
    """Load a candidate-context file without making it a run-fatal dependency."""

    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return "", "missing"
    except (OSError, UnicodeError, ValueError):
        return "", "unreadable"

    clipped = clip_with_marker(text, budget, marker)
    return clipped, "truncated" if clipped != text else "loaded"


def _positive_int_from_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed
