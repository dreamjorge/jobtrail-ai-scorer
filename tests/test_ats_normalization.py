"""Tests for Lever normalization and shared ATS data helpers."""

from __future__ import annotations

from typing import Any, Mapping

from jobtrail_ai_scorer.sources import NormalizedJob
from jobtrail_ai_scorer.sources.ats_common import _bounded, _strip_html
from jobtrail_ai_scorer.sources.lever import SOURCE_NAME, normalize_lever_posting


def _full_lever_posting(
    *,
    posting_id: str | None = "abc-123",
    title: str | None = "Senior Python Developer",
    description: str | None = "<p>Build amazing things.</p><p>More details.</p>",
    apply_url: str | None = "https://jobs.lever.co/acme/abc-123",
    location: str | None = "Mexico City",
    commitment: str | None = "Full-time",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a representative Lever posting, omitting optional fields as asked."""

    record: dict[str, Any] = {}
    if posting_id is not None:
        record["id"] = posting_id
    if title is not None:
        record["text"] = title
    if description is not None:
        record["description"] = description
    if apply_url is not None:
        record["applyUrl"] = apply_url
    categories: dict[str, Any] = {}
    if location is not None:
        categories["location"] = location
    if commitment is not None:
        categories["commitment"] = commitment
    if categories:
        record["categories"] = categories
    if extra:
        record.update(extra)
    return record


def test_strip_html_strips_tags_and_keeps_text():
    assert _strip_html("<p>Hello <b>world</b>!</p>") == "Hello world!"


def test_strip_html_collapses_whitespace_between_blocks():
    assert _strip_html("<p>Line one.</p><p>Line two.</p>") == "Line one. Line two."


def test_strip_html_handles_html_entities():
    assert _strip_html('<p>Tom &amp; Jerry &quot;friends&quot;</p>') == (
        'Tom & Jerry "friends"'
    )


def test_strip_html_returns_empty_string_for_empty_or_whitespace_only():
    assert _strip_html("") == ""
    assert _strip_html("<p>   </p>") == ""


def test_strip_html_preserves_inline_text_only():
    assert _strip_html("plain text only") == "plain text only"


def test_bounded_truncates_to_min_of_length_and_cap():
    assert _bounded([1, 2, 3, 4, 5], 3) == [1, 2, 3]
    assert _bounded([1, 2, 3, 4, 5], 100) == [1, 2, 3, 4, 5]


def test_bounded_uses_min_length_when_cap_is_larger():
    assert _bounded([1, 2], 50) == [1, 2]


def test_bounded_stamps_retrieved_at_when_provided():
    assert _bounded(
        [{"id": "a"}, {"id": "b"}], 10, retrieved_at="2026-09-08T10:00:00Z"
    ) == [
        {"id": "a", "retrieved_at": "2026-09-08T10:00:00Z"},
        {"id": "b", "retrieved_at": "2026-09-08T10:00:00Z"},
    ]


def test_bounded_does_not_stamp_retrieved_at_when_omitted():
    assert _bounded([{"id": "a"}], 10) == [{"id": "a"}]


def test_bounded_returns_empty_list_for_empty_input_or_zero_cap():
    assert _bounded([], 5) == []
    assert _bounded([1, 2, 3], 0) == []


def test_bounded_does_not_mutate_input_list():
    items = [1, 2, 3, 4, 5]
    out = _bounded(items, 3)
    assert items == [1, 2, 3, 4, 5]
    assert out == [1, 2, 3]


def test_bounded_returns_copies_of_dict_items():
    items = [{"id": "a"}]
    out = _bounded(items, 5, retrieved_at="2026-09-08T10:00:00Z")
    out[0]["id"] = "mutated"
    assert items[0] == {"id": "a"}


def test_normalize_lever_posting_maps_required_fields():
    job = normalize_lever_posting(
        _full_lever_posting(),
        company="acme",
        profile_name="backend",
        retrieved_at="2026-09-08T10:00:00Z",
    )
    assert job == NormalizedJob(
        source=SOURCE_NAME,
        source_job_id="abc-123",
        title="Senior Python Developer",
        company="acme",
        description="Build amazing things. More details.",
        source_url="https://jobs.lever.co/acme/abc-123",
        location="Mexico City / Full-time",
        search_profile="backend",
        retrieved_at="2026-09-08T10:00:00Z",
    )


def test_normalize_lever_posting_combines_location_and_commitment():
    job = normalize_lever_posting(
        _full_lever_posting(location="Remote", commitment="Contract"),
        company="acme", profile_name="default", retrieved_at=None,
    )
    assert job.location == "Remote / Contract"


def test_normalize_lever_posting_supports_each_location_component_alone():
    location_only = normalize_lever_posting(
        _full_lever_posting(location="Queretaro", commitment=None),
        company="acme", profile_name="default", retrieved_at=None,
    )
    commitment_only = normalize_lever_posting(
        _full_lever_posting(location=None, commitment="Full-time"),
        company="acme", profile_name="default", retrieved_at=None,
    )
    neither = normalize_lever_posting(
        _full_lever_posting(location=None, commitment=None),
        company="acme", profile_name="default", retrieved_at=None,
    )
    assert location_only.location == "Queretaro"
    assert commitment_only.location == "Full-time"
    assert neither.location is None


def test_normalize_lever_posting_strips_html_and_handles_missing_description():
    html_job = normalize_lever_posting(
        _full_lever_posting(description="<ul><li>Requirement 1</li><li>Requirement 2</li></ul>"),
        company="acme", profile_name="default", retrieved_at=None,
    )
    missing_job = normalize_lever_posting(
        _full_lever_posting(description=None),
        company="acme", profile_name="default", retrieved_at=None,
    )
    assert html_job.description == "Requirement 1 Requirement 2"
    assert missing_job.description is None


def test_normalize_lever_posting_preserves_company_and_coerces_id():
    job = normalize_lever_posting(
        _full_lever_posting(posting_id=42),  # type: ignore[arg-type]
        company="globex", profile_name="default", retrieved_at=None,
    )
    missing_id = normalize_lever_posting(
        _full_lever_posting(posting_id=None),
        company="acme", profile_name="default", retrieved_at=None,
    )
    assert job.company == "globex"
    assert job.source_job_id == "42"
    assert missing_id.source_job_id is None


def test_normalize_lever_posting_omits_retrieved_at_when_none():
    job = normalize_lever_posting(
        _full_lever_posting(), company="acme", profile_name="default", retrieved_at=None
    )
    assert job.retrieved_at is None
