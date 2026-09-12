"""Focused validation tests for manually supplied Jobright postings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jobtrail_ai_scorer.jobright_manual import (
    SOURCE_NAME,
    JobrightManualInput,
    JobrightManualJob,
    normalize_jobright_manual,
)


def _input(**overrides):
    values = {
        "url": " HTTPS://Jobright.ai/jobs/123#details ",
        "title": "Senior Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Private details for scoring",
    }
    values.update(overrides)
    return values


def test_normalizes_https_url_and_uses_stable_url_id():
    first = normalize_jobright_manual(_input())
    second = normalize_jobright_manual(_input(url="https://jobright.ai/jobs/123#other"))

    assert first.source == SOURCE_NAME == "jobright_manual"
    assert first.source_url == "https://jobright.ai/jobs/123"
    assert first.source_job_id == second.source_job_id
    assert first.source_job_id


def test_rejects_non_https_or_malformed_urls():
    for url in (
        "http://jobright.ai/jobs/123",
        "jobright.ai/jobs/123",
        "https://",
        "https://\ud800.example/jobs/123",
    ):
        with pytest.raises(ValidationError):
            JobrightManualInput.model_validate(_input(url=url))


def test_requires_title_company_and_location():
    for field in ("title", "company", "location"):
        values = _input()
        values[field] = " "
        with pytest.raises(ValidationError):
            JobrightManualInput.model_validate(values)


def test_repr_does_not_expose_private_description():
    job = normalize_jobright_manual(_input())

    assert "Private details for scoring" not in repr(job)


def test_source_is_fixed_on_direct_model_construction():
    job = JobrightManualJob(
        source_url="https://jobright.ai/jobs/123",
        source_job_id="123",
        title="Senior Engineer",
        company="Acme",
        location="Remote",
        source="spoofed-source",
    )

    assert job.source == SOURCE_NAME
    assert job.scorer_input["source"] == SOURCE_NAME
    assert job.to_import_payload()["source"] == SOURCE_NAME


def test_description_is_only_retained_in_scorer_input():
    job = normalize_jobright_manual(_input())

    assert job.to_import_payload() == {
        "source": SOURCE_NAME,
        "sourceJobId": job.source_job_id,
        "company": "Acme",
        "position": "Senior Engineer",
        "jobUrl": "https://jobright.ai/jobs/123",
        "location": "Remote",
    }
    assert job.scorer_input["description"] == "Private details for scoring"
    assert "description" not in job.to_import_payload()


def test_optional_description_is_omitted_from_scorer_input_when_absent():
    job = normalize_jobright_manual(_input(description=None))
    assert job.scorer_input == {
        "title": "Senior Engineer",
        "company": "Acme",
        "location": "Remote",
        "source": SOURCE_NAME,
        "sourceUrl": "https://jobright.ai/jobs/123",
    }


def test_bounds_and_unexpected_fields_are_rejected():
    with pytest.raises(ValidationError):
        JobrightManualInput.model_validate(_input(title="x" * 201))
    with pytest.raises(ValidationError):
        JobrightManualInput.model_validate(_input(description="x" * 12001))
    with pytest.raises(ValidationError):
        JobrightManualInput.model_validate(_input(unexpected="ignored"))
