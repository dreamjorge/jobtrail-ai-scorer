"""CI guardrail: assert the Hermes SOUL/SKILL apply-gate policy.

These tests pin the runtime automation rules that protect the candidate from
unintentional applications. They load local fixture mocks (not the operator's
runtime files under ``/DATA``) and assert the literal ``explicit confirmation``
and ``never submit`` markers that the production ``SOUL.md``/``SKILL.md`` must
contain. Drift in the fixtures fails CI loudly with a focused diff.
"""

from __future__ import annotations

import re
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "hermes"
SOUL_FIXTURE = FIXTURES_DIR / "SOUL.md"
SKILL_FIXTURE = (
    FIXTURES_DIR / "skills" / "jobtrail-automation" / "SKILL.md"
)

# Required apply-gate phrases. The substring case is preserved so it can be
# searched verbatim in the production documents.
EXPLICIT_CONFIRMATION = "explicit confirmation"
NEVER_SUBMIT = "never submit"
WITHOUT_EXPLICIT_CONFIRMATION = "without an explicit confirmation"


def _apply_section(text: str) -> str:
    """Return the slice of ``text`` that starts at the first apply/submit
    paragraph. The slice includes everything from the first heading or
    paragraph that mentions ``apply`` or ``submit`` down to the next top-level
    heading (``## ``) or end of document. This keeps assertions scoped to the
    apply context instead of the whole document.
    """

    markers = ("apply", "submit", "application")
    pattern = re.compile("|".join(re.escape(m) for m in markers), re.IGNORECASE)

    lines = text.splitlines(keepends=True)

    # Prefer a heading line that mentions a marker so we don't latch onto an
    # incidental word inside a body paragraph; fall back to the earliest
    # paragraph that mentions a marker.
    start_index: int | None = None
    fallback_index: int | None = None
    for i, line in enumerate(lines):
        if not pattern.search(line):
            continue
        if line.lstrip().startswith("#"):
            start_index = i
            break
        if fallback_index is None:
            fallback_index = i
    chosen = start_index if start_index is not None else fallback_index
    if chosen is None:
        return ""

    end_index = len(lines)
    for j in range(chosen + 1, len(lines)):
        if lines[j].lstrip().startswith("##"):
            end_index = j
            break
    return "".join(lines[chosen:end_index])


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_soul_fixture_exists():
    assert SOUL_FIXTURE.is_file(), (
        f"Missing fixture: {SOUL_FIXTURE}. The runtime SOUL.md must be mirrored "
        "as a CI-only fixture so the apply-gate contract is enforceable in PRs."
    )


def test_skill_fixture_exists():
    assert SKILL_FIXTURE.is_file(), (
        f"Missing fixture: {SKILL_FIXTURE}. The runtime SKILL.md must be mirrored "
        "as a CI-only fixture so the apply-gate contract is enforceable in PRs."
    )


def test_soul_apply_section_requires_explicit_confirmation():
    text = _read(SOUL_FIXTURE)
    section = _apply_section(text)
    assert section, "SOUL.md has no apply/submit paragraph to scope the gate"
    assert EXPLICIT_CONFIRMATION in section, (
        "SOUL.md apply context must require 'explicit confirmation' before any "
        f"apply/submit action. Apply section seen:\n---\n{section}\n---"
    )


def test_skill_requires_explicit_confirmation():
    text = _read(SKILL_FIXTURE)
    assert EXPLICIT_CONFIRMATION in text, (
        "SKILL.md must require 'explicit confirmation' anywhere the apply intent "
        "is described.\n--- whole file ---\n" + text
    )


def test_runtime_files_never_submit_without_confirmation():
    # Each fixture must include at least one of the two deny phrases (OR, not
    # AND): a document is compliant whether it says "never submit" or
    # "without an explicit confirmation" (or both).
    deny_phrases = (NEVER_SUBMIT, WITHOUT_EXPLICIT_CONFIRMATION)
    soul = _apply_section(_read(SOUL_FIXTURE))
    skill = _read(SKILL_FIXTURE)
    soul_match = any(phrase.lower() in soul.lower() for phrase in deny_phrases)
    skill_match = any(phrase.lower() in skill.lower() for phrase in deny_phrases)
    assert soul_match, (
        "SOUL.md apply section missing both deny phrases. Add 'never submit' "
        f"or 'without an explicit confirmation' to the apply context.\n"
        f"--- SOUL apply section ---\n{soul}\n---"
    )
    assert skill_match, (
        "SKILL.md missing both deny phrases. Add 'never submit' or "
        "'without an explicit confirmation'.\n--- SKILL.md ---\n" + skill
    )


def test_fixtures_do_not_leak_runtime_data():
    """Guardrail against accidentally committing operator runtime content.

    The fixtures must remain pure contract mocks. Any path, hostname, profile
    identifier, or credential marker that points at real runtime data must
    fail CI.
    """

    forbidden_substrings = [
        "/DATA/",  # runtime paths outside the repo
        "/AppData/",  # operator runtime roots
        "profile.md secret",
        "BEGIN PRIVATE KEY",
        "LinkedIn-Profile-URL",
    ]
    for path in (SOUL_FIXTURE, SKILL_FIXTURE):
        text = _read(path)
        for forbidden in forbidden_substrings:
            assert forbidden.lower() not in text.lower(), (
                f"{path} leaks runtime/secret content ({forbidden!r}). "
                "Fixtures must be minimal mocks only."
            )
