"""Tests that committed example configs and templates contain no leaked private data.

The repo ships a single canonical example at ``scripts/scorer-config.example.yaml``
and a thin pointer at the repo root (``config.example.yaml``). Both must remain
free of operator runtime data:

- RFC1918 private IPv4 ranges (``10.x``, ``172.16-31.x``, ``192.168.x``).
- Private runtime path tokens (``/DATA/``, ``/AppData/``).
- Common credential prefixes (``sk-…``, ``ghp_…``, ``gho_…``, ``github_pat_…``,
  ``xox[abprs]-…``).

The tests fail loudly with a focused diff so a regression that re-introduces a
private IP, a runtime path, or a token-shaped string is caught at PR time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

CANONICAL_EXAMPLE = ROOT / "scripts" / "scorer-config.example.yaml"
POINTER_EXAMPLE = ROOT / "config.example.yaml"
PROFILE_EXAMPLE = ROOT / "candidate-profile.example.md"
DOCKERFILE = ROOT / "Dockerfile"
DOCKER_COMPOSE = ROOT / "docker-compose.yml"

# Build the scanned list from a single source of truth and dedupe so the
# canonical example is only listed once even though it also matches the
# ``scripts/*.example.*`` glob.
_EXAMPLE_GLOB = tuple(sorted((ROOT / "scripts").glob("*.example.*")))
EXAMPLES_AND_TEMPLATES = tuple(
    dict.fromkeys(
        (
            CANONICAL_EXAMPLE,
            POINTER_EXAMPLE,
            PROFILE_EXAMPLE,
            DOCKERFILE,
            DOCKER_COMPOSE,
        )
        + _EXAMPLE_GLOB
    )
)


# Forbidden private IPv4 patterns (RFC1918).
PRIVATE_IP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
)

# Private path tokens that must never appear in committed examples.
FORBIDDEN_PATH_SUBSTRINGS: tuple[str, ...] = (
    "/DATA/",
    "/AppData/",
)

# Credential prefix patterns. The post-prefix length is intentionally short
# (>=8 chars) to allow documentation like "sk- prefix" without matching, but
# still catches any plausible secret pasted into an example.
FORBIDDEN_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{8,}"),
    re.compile(r"\bsk_test_[A-Za-z0-9]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgho_[A-Za-z0-9]{8,}"),
    re.compile(r"\bghu_[A-Za-z0-9]{8,}"),
    re.compile(r"\bghs_[A-Za-z0-9]{8,}"),
    re.compile(r"\bghr_[A-Za-z0-9]{8,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{16,}"),
)


def _scan(path: Path) -> list[str]:
    """Return a list of human-readable findings for ``path``.

    The findings are kept short so the failure message points straight at the
    offending match without dumping the whole file.
    """

    findings: list[str] = []
    text = path.read_text(encoding="utf-8")

    for pattern in PRIVATE_IP_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"private IPv4 {match.group(0)!r}")

    for needle in FORBIDDEN_PATH_SUBSTRINGS:
        if needle in text:
            findings.append(f"forbidden runtime path {needle!r}")

    for pattern in FORBIDDEN_TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"token-shaped string {match.group(0)!r}")

    return findings


def test_examples_and_templates_exist() -> None:
    """All scanned files must exist; the list is the single source of truth."""

    missing = [path for path in EXAMPLES_AND_TEMPLATES if not path.is_file()]
    assert not missing, (
        "Expected committed examples/templates are missing:\n"
        + "\n".join(f"  - {path}" for path in missing)
    )


def test_canonical_example_is_a_single_source_of_truth() -> None:
    """The canonical example must contain the canonical YAML settings block.

    The pointer file at the repo root is allowed to be a short redirect; the
    canonical example is the only place where ``jobtrail_base_url``,
    ``candidate_profile_path``, ``provider``, and ``marker`` are defined.
    """

    text = CANONICAL_EXAMPLE.read_text(encoding="utf-8")
    assert "jobtrail_base_url:" in text, (
        f"Canonical example {CANONICAL_EXAMPLE} must declare jobtrail_base_url."
    )
    assert "candidate_profile_path:" in text, (
        f"Canonical example {CANONICAL_EXAMPLE} must declare candidate_profile_path."
    )
    assert "provider:" in text, (
        f"Canonical example {CANONICAL_EXAMPLE} must declare provider."
    )
    assert "marker:" in text, (
        f"Canonical example {CANONICAL_EXAMPLE} must declare marker."
    )


def test_pointer_file_redirects_to_canonical_example() -> None:
    """The repo-root pointer must reference the canonical example by relative path."""

    text = POINTER_EXAMPLE.read_text(encoding="utf-8")
    canonical_relative = CANONICAL_EXAMPLE.relative_to(ROOT).as_posix()
    assert canonical_relative in text, (
        f"Pointer file {POINTER_EXAMPLE} must reference the canonical example at "
        f"{canonical_relative} so there is exactly one source of truth."
    )


def test_pointer_file_does_not_duplicate_yaml_settings() -> None:
    """The pointer must not redeclare YAML settings; those live in the canonical."""

    text = POINTER_EXAMPLE.read_text(encoding="utf-8")
    forbidden_settings = (
        "jobtrail_base_url:",
        "candidate_profile_path:",
        "candidate_cv_path:",
        "provider:",
        "marker:",
        "hermes_executable:",
        "hermes_profile:",
        "openai_endpoint:",
    )
    duplicates = [setting for setting in forbidden_settings if setting in text]
    assert not duplicates, (
        f"Pointer file {POINTER_EXAMPLE} must not duplicate YAML settings; they "
        "live in the canonical example. Found: " + ", ".join(duplicates)
    )


@pytest.mark.parametrize(
    "path",
    EXAMPLES_AND_TEMPLATES,
    ids=lambda p: p.relative_to(ROOT).as_posix(),
)
def test_example_has_no_leaked_private_data(path: Path) -> None:
    """Each committed example/template must contain no private IP, runtime path,
    or token-shaped string.
    """

    findings = _scan(path)
    assert not findings, (
        f"{path.relative_to(ROOT)} leaks private data; fix and re-run:\n  - "
        + "\n  - ".join(findings)
    )


def test_private_ip_pattern_set_is_well_formed() -> None:
    """Sanity check: the private IPv4 patterns must catch the canonical bad
    examples. If this test ever fails the regex set is the bug, not the file.
    """

    bad_samples = (
        "10.0.0.1",
        "192.168.1.10",
        "172.16.0.1",
        "172.20.30.40",
        "172.31.255.254",
    )
    good_samples = (
        "127.0.0.1",
        "8.8.8.8",
        "172.15.255.255",
        "172.32.0.0",
        "9.10.11.12",
    )
    for sample in bad_samples:
        assert any(p.search(sample) for p in PRIVATE_IP_PATTERNS), (
            f"private IP pattern set missed bad sample {sample!r}"
        )
    for sample in good_samples:
        assert not any(p.search(sample) for p in PRIVATE_IP_PATTERNS), (
            f"private IP pattern set false-matched good sample {sample!r}"
        )


# ---------------------------------------------------------------------------
# Triangulation: ensure the scanner catches every category of leak on demand.
# These tests use tmp_path so the repo is never polluted with leaked data.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leak",
    [
        pytest.param(
            "jobtrail_base_url: http://192.168.10.20:8000\n", id="private-ipv4-192"
        ),
        pytest.param(
            "jobtrail_base_url: http://10.20.30.40:8000\n", id="private-ipv4-10"
        ),
        pytest.param(
            "jobtrail_base_url: http://172.24.0.5:8000\n", id="private-ipv4-172"
        ),
        pytest.param(
            "candidate_cv_path: /DATA/AppData/jobtrail/candidate-cv.md\n",
            id="private-path-data",
        ),
        pytest.param(
            "candidate_cv_path: /AppData/jobtrail/candidate-cv.md\n",
            id="private-path-appdata",
        ),
        pytest.param(
            "openai_api_key_env: sk-abcdef1234567890abcd\n", id="openai-token"
        ),
        pytest.param(
            "openai_api_key_env: ghp_abcdef1234567890abcd\n", id="github-token"
        ),
        pytest.param(
            "token: github_pat_11ABCDEFG0_abcdefghij1234567890\n", id="github-pat"
        ),
        pytest.param("token: xoxb-1234567890-abcdefghij\n", id="slack-token"),
        pytest.param(
            "google_api_key: AIzaSyA_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456\n",
            id="google-api-key",
        ),
    ],
)
def test_scanner_catches_injected_leaks(tmp_path: Path, leak: str) -> None:
    """A deliberate injection in a tmp file must be flagged by ``_scan``."""

    target = tmp_path / "leaky.example.yaml"
    target.write_text(leak, encoding="utf-8")

    findings = _scan(target)
    assert findings, f"Scanner missed deliberate leak in tmp fixture: {leak!r}"


@pytest.mark.parametrize(
    "clean",
    [
        pytest.param("jobtrail_base_url: http://localhost:3000\n", id="localhost"),
        pytest.param("jobtrail_base_url: http://127.0.0.1:8000\n", id="loopback"),
        pytest.param(
            "jobtrail_base_url: https://api.example.test/v1\n", id="public-hostname"
        ),
        pytest.param(
            "marker: sk- is a documented token prefix example\n",
            id="short-token-prefix",
        ),
        pytest.param(
            "candidate_cv_path: /absolute/path/to/your/cv.md\n", id="placeholder-path"
        ),
    ],
)
def test_scanner_does_not_flag_clean_placeholders(tmp_path: Path, clean: str) -> None:
    """Public placeholders must not be flagged by ``_scan``."""

    target = tmp_path / "clean.example.yaml"
    target.write_text(clean, encoding="utf-8")

    findings = _scan(target)
    assert not findings, (
        f"Scanner false-matched clean placeholder {clean!r}: {findings}"
    )
