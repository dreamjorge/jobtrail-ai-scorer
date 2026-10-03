"""Load the public, rules-only matching strategy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal


# Package data is installed beside this module, independent of the caller's CWD.
DEFAULT_STRATEGY_PATH = Path(__file__).resolve().parent / "data" / "matching-strategy.md"
DEFAULT_MAX_CHARS = 12_000
REQUIRED_SECTIONS = (
    "Target roles",
    "Required signals",
    "Equivalent skills",
    "Adjacent roles",
    "Exclusion rules",
    "Location and seniority tolerance",
    "Evidence policy",
)
StrategyStatus = Literal[
    "loaded", "missing", "unreadable", "oversized", "invalid", "forbidden"
]

_FORBIDDEN = re.compile(
    r"(?:\b(?:candidate\s+)?(?:cv|resume|credentials?|secrets?)\b|"
    r"\bcurriculum\s+vitae\b|\bn8n\b|\bwebhooks?\b|"
    r"\bprivate\s+(?:integrations?|workflows?|webhooks?)\b|"
    r"\b(?:api[-_ ]?keys?|access[-_ ]?tokens?|bearer)\b|"
    r"\.env\b|/home/|\\users\\|~/)",
    re.IGNORECASE,
)
_FORBIDDEN_PATH = re.compile(
    r"(?:^|[\\/_.-])(?:candidate[-_. ]?(?:cv|resume|profile)|private|"
    r"credentials?|secrets?|n8n|webhooks?)(?:$|[\\/_.-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MatchingStrategy:
    """A validated public strategy, or an empty safe-unavailable result."""

    available: bool
    status: StrategyStatus
    sections: dict[str, str]
    prompt_context: str = ""

    @classmethod
    def unavailable(cls, status: StrategyStatus) -> "MatchingStrategy":
        return cls(False, status, {}, "")


def load_matching_strategy(
    path: Path | str | None = DEFAULT_STRATEGY_PATH,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> MatchingStrategy:
    """Load and validate a bounded public strategy without raising file errors."""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if path is None:
        return MatchingStrategy.unavailable("missing")
    path_text = str(path)
    if _FORBIDDEN_PATH.search(path_text):
        return MatchingStrategy.unavailable("forbidden")
    try:
        strategy_path = Path(path)
        with strategy_path.open("r", encoding="utf-8") as stream:
            # Read only one character beyond the configured limit.  In
            # particular, do not use Path.read_text(), which loads an
            # oversized strategy before it can be rejected.
            text = stream.read(max_chars + 1)
    except FileNotFoundError:
        return MatchingStrategy.unavailable("missing")
    except (OSError, UnicodeError, ValueError):
        return MatchingStrategy.unavailable("unreadable")
    if len(text) > max_chars:
        return MatchingStrategy.unavailable("oversized")
    if _FORBIDDEN.search(text):
        return MatchingStrategy.unavailable("forbidden")

    sections = _extract_sections(text)
    if any(section not in sections for section in REQUIRED_SECTIONS):
        return MatchingStrategy.unavailable("invalid")
    if any(not sections[section].strip() for section in REQUIRED_SECTIONS):
        return MatchingStrategy.unavailable("invalid")
    context = "\n\n".join(
        f"## {section}\n{sections[section]}" for section in REQUIRED_SECTIONS
    )
    return MatchingStrategy(True, "loaded", sections, context)


def _extract_sections(text: str) -> dict[str, str]:
    headings = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        name = heading.group(1).strip()
        canonical = next(
            (required for required in REQUIRED_SECTIONS if required.casefold() == name.casefold()),
            None,
        )
        if canonical is None:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[canonical] = text[heading.end():end].strip()
    return sections
