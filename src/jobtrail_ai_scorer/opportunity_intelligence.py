"""Conservative, extractive public evidence. No runtime wiring or implicit trust.

Create one resolver with one search adapter and fetcher per sequential run.
Injected transports/clocks are trusted seams; page content is never executable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from .public_http import FetchResult
from .public_search import PublicSearchRequest, PublicSearchResult, _candidate_url

MAX_PAGE = 512 * 1024
MAX_NODES = 2000
MAX_DEPTH = 20
MAX_LINKS = 200
MAX_RECORDS = 40


def _norm(value):
    return ' '.join(value.casefold().split()) if isinstance(value, str) else ''


def _text(value, limit=500):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(ord(c) < 32 and c not in '\n\t\r' for c in value))


def _date(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except ValueError:
        return None


def _checked(value):
    if not isinstance(value, str) or len(value) > 40 or 'T' not in value:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def _url(value):
    return _candidate_url(value)


def _host(url):
    return urlsplit(url).hostname


@dataclass(frozen=True)
class Citation:
    url: str
    excerpt: str
    checked_at: str
    kind: str

    def __post_init__(self):
        if (not _url(self.url) or not _text(self.excerpt, 1000)
                or _checked(self.checked_at) is None
                or self.kind not in ('public_citation', 'user_confirmed', 'ownership',
                                     'identity', 'activity', 'company_reported')):
            raise ValueError('invalid public citation')


_ATS_SOURCE_NAMES = frozenset({"lever", "greenhouse", "workday", "ashby", "bamboohr", "workable", "smartrecruiters"})
_GENERIC_ATS_HOST_FAMILIES = frozenset({"workday", "greenhouse", "lever", "ashby", "bamboohr", "workable", "smartrecruiters"})
# Public board/API domains used by supported ATS integrations. Match only
# complete DNS labels so an employer such as "Greenhouse Technologies" or a
# lookalike domain does not get discarded.
_ATS_PROVIDER_DOMAINS = (
    "lever.co", "greenhouse.io", "myworkdayjobs.com", "ashbyhq.com",
    "bamboohr.com", "workable.com", "smartrecruiters.com",
)


def _is_ats_provider_host(value):
    candidate = value.strip().casefold().rstrip(".")
    if "://" in candidate:
        candidate = urlsplit(candidate).hostname or ""
    elif "/" in candidate:
        return False
    return any(candidate == domain or candidate.endswith("." + domain)
               for domain in _ATS_PROVIDER_DOMAINS)


def _sanitize_ats_company(company, source):
    if company is None:
        return None
    if _is_ats_provider_host(company):
        return None
    if source and source.casefold() in _ATS_SOURCE_NAMES:
        if company.strip().casefold() in _GENERIC_ATS_HOST_FAMILIES:
            return None
    return company


@dataclass(frozen=True)
class PublicJobIdentity:
    company: str
    title: str
    location: str
    original_url: str
    source: str
    source_job_id: str
    requisition_id: str | None = None
    requisition_namespace: str | None = None

    def __post_init__(self):
        # Validate searchability using original company (search needs a string).
        PublicSearchRequest(self.company, self.title, self.location)
        if (not _url(self.original_url) or not _text(self.source, 80)
                or not _text(self.source_job_id, 160)):
            raise ValueError('invalid public identity')
        # Sanitize: generic ATS platform names cannot serve as employer
        # ownership anchors; strip them to None.
        _sanitized = _sanitize_ats_company(self.company, self.source)
        if _sanitized is None:
            object.__setattr__(self, 'company', None)
        for value in (self.source, self.source_job_id, self.requisition_id, self.requisition_namespace):
            if value is not None and (not _text(value, 160) or not re.fullmatch(r"[\w .,+()'/-]+", value)):
                raise ValueError('invalid public identifier')
        if (self.requisition_id is None) != (self.requisition_namespace is None):
            raise ValueError('requisition requires explicit namespace')


@dataclass(frozen=True)
class TrustedEmployerContext:
    company: str
    hosts: tuple[str, ...]
    careers_url: str
    provenance: Citation

    def __post_init__(self):
        PublicSearchRequest(self.company, 'Careers', 'Public')
        if (type(self.hosts) is not tuple or not 1 <= len(self.hosts) <= 5
                or any(not isinstance(h, str) or _url('https://' + h + '/') != 'https://' + h + '/'
                       or _host('https://' + h + '/') != h for h in self.hosts)
                or not _url(self.careers_url) or _host(self.careers_url) not in self.hosts
                or type(self.provenance) is not Citation
                or self.provenance.kind not in ('public_citation', 'user_confirmed')
                or _host(self.provenance.url) not in self.hosts):
            raise ValueError('invalid explicit employer anchor')
        # Generic ATS provider hosts cannot themselves be employer trust anchors.
        if any(_is_ats_provider_host(h) for h in self.hosts):
            raise ValueError('provider hostname is not employer authority')


def parse_employer_contexts(raw: str) -> tuple[TrustedEmployerContext, ...]:
    """Operator assertions only; closed schema, bounded text, generic errors."""
    try:
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > 16 * 1024:
            raise ValueError
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        entries = json.loads(raw, object_pairs_hook=pairs)
        if type(entries) is not list or len(entries) > 10:
            raise ValueError
        contexts = []
        for entry in entries:
            if type(entry) is not dict or set(entry) != {'company', 'hosts', 'careers_url', 'provenance'}:
                raise ValueError
            proof = entry['provenance']
            if type(proof) is not dict or set(proof) != {'url', 'excerpt', 'checked_at', 'kind'}:
                raise ValueError
            if type(entry['hosts']) is not list:
                raise ValueError
            context = TrustedEmployerContext(entry['company'], tuple(entry['hosts']),
                                             entry['careers_url'], Citation(**proof))
            if any(_norm(c.company) == _norm(context.company) for c in contexts):
                raise ValueError
            contexts.append(context)
        return tuple(contexts)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('invalid employer contexts') from None


@dataclass(frozen=True)
class CompanyClaim:
    field: str
    value: str
    citation: Citation
    attribution: str = 'company_reported'


@dataclass(frozen=True)
class CompanyBrief:
    status: str = 'unavailable'
    claims: tuple[CompanyClaim, ...] = ()


@dataclass(frozen=True)
class OpportunityResult:
    status: str
    source_url: str | None
    official_url: str | None = None
    candidate_url: str | None = None
    candidates: tuple[str, ...] = ()
    checked_at: str | None = None
    citations: tuple[Citation, ...] = ()
    company_brief: CompanyBrief = CompanyBrief()
    reason: str | None = None


class SearchAdapter(Protocol):
    def search(self, request: PublicSearchRequest) -> PublicSearchResult: ...


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...
