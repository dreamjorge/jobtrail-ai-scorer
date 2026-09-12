"""Validation and normalization for manually entered Jobright postings."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .sources import NormalizedJob

SOURCE_NAME = "jobright_manual"
_MAX_TEXT = 200
_MAX_DESCRIPTION = 12_000
_MAX_URL = 2_048


def _text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty or whitespace only")
    return value


def _normalize_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if parsed.scheme.lower() != "https" or hostname is None:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        port = parsed.port
        host = hostname.encode("idna").decode("ascii").lower()
    except (TypeError, UnicodeError, ValueError):
        raise ValueError("url must be an absolute HTTPS URL") from None
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit(("https", netloc, path, parsed.query, ""))


class JobrightManualInput(BaseModel):
    """The deliberately small, strict set of fields accepted from a user."""

    model_config = ConfigDict(extra="forbid", strict=True)

    url: str = Field(max_length=_MAX_URL)
    title: str = Field(max_length=_MAX_TEXT)
    company: str = Field(max_length=_MAX_TEXT)
    location: str = Field(max_length=_MAX_TEXT)
    description: str | None = Field(default=None, max_length=_MAX_DESCRIPTION)

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("url must be a string")
        normalized = _normalize_url(value)
        if len(normalized) > _MAX_URL:
            raise ValueError("url is too long")
        return normalized

    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def validate_required_text(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return _text(value)

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("description must be a string")
        value = value.strip()
        return value or None


@dataclass(frozen=True)
class JobrightManualJob:
    """Normalized import data plus the private data needed by the scorer."""

    source_url: str
    source_job_id: str
    title: str
    company: str
    location: str
    description: str | None = field(default=None, repr=False)
    source: str = SOURCE_NAME

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", SOURCE_NAME)

    @property
    def scorer_input(self) -> dict[str, str]:
        result = {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "source": self.source,
            "sourceUrl": self.source_url,
        }
        if self.description is not None:
            result["description"] = self.description
        return result

    def to_import_payload(self) -> dict[str, Any]:
        """Use the shared source payload while never importing private text."""

        return NormalizedJob(
            source=self.source,
            source_job_id=self.source_job_id,
            title=self.title,
            company=self.company,
            source_url=self.source_url,
            location=self.location,
        ).to_import_payload()


def normalize_jobright_manual(
    value: JobrightManualInput | Mapping[str, Any],
) -> JobrightManualJob:
    """Validate one manual record and derive its deterministic source identity."""

    validated = value if isinstance(value, JobrightManualInput) else JobrightManualInput.model_validate(value)
    source_job_id = hashlib.sha256(validated.url.encode("utf-8")).hexdigest()
    return JobrightManualJob(
        source_url=validated.url,
        source_job_id=source_job_id,
        title=validated.title,
        company=validated.company,
        location=validated.location,
        description=validated.description,
    )


__all__ = [
    "JobrightManualInput",
    "JobrightManualJob",
    "SOURCE_NAME",
    "normalize_jobright_manual",
]
