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
        PublicSearchRequest(self.company, self.title, self.location)
        if (not _url(self.original_url) or not _text(self.source, 80)
                or not _text(self.source_job_id, 160)):
            raise ValueError('invalid public identity')
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
        if any(h in ('jobs.lever.co', 'boards.greenhouse.io', 'job-boards.greenhouse.io') for h in self.hosts):
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


class _Page(HTMLParser):
    """Bounded literal links and LD+JSON only; ignore all other scripts/styles."""
    def __init__(self, text, mime):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.blocks = []
        self.script = None
        self.ignored = None
        self.anchor = None
        self.steps = 0
        if len(text) > MAX_PAGE or len(text.encode('utf-8')) > MAX_PAGE:
            raise ValueError('page too large')
        if mime == 'application/json':
            self.blocks = [text]
        elif mime == 'text/html':
            self.feed(text)
            self.close()
            if self.script is not None:
                raise ValueError('unterminated structured evidence')
        else:
            raise ValueError('unsupported evidence type')
        self.records = []
        nodes = 0
        for block in self.blocks:
            try:
                # Duplicate keys can hide conflicting dates/identity: reject the block.
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise ValueError('duplicate JSON key')
                        result[key] = value
                    return result
                root = json.loads(block, object_pairs_hook=pairs)
            except (ValueError, RecursionError):
                raise ValueError('invalid structured evidence') from None
            stack = [(root, 0)]
            while stack:
                node, depth = stack.pop()
                nodes += 1
                if nodes > MAX_NODES or depth > MAX_DEPTH:
                    raise ValueError('structured evidence too complex')
                if isinstance(node, dict):
                    types = node.get('@type')
                    if types in ('JobPosting', 'Organization') or (
                            isinstance(types, list) and any(t in ('JobPosting', 'Organization') for t in types)):
                        self.records.append(node)
                        if len(self.records) > MAX_RECORDS:
                            raise ValueError('too many records')
                    stack.extend((v, depth + 1) for v in node.values())
                elif isinstance(node, list):
                    stack.extend((v, depth + 1) for v in node)

    def handle_starttag(self, tag, attrs):
        self.steps += 1
        if self.steps > MAX_NODES:
            raise ValueError('too many HTML elements')
        attrs = dict(attrs)
        if tag in ('script', 'style'):
            self.ignored = tag
            self.script = '' if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json' else None
        if tag == 'a' and self.ignored is None and len(self.links) < MAX_LINKS:
            self.anchor = [attrs.get('href', ''), '']

    def handle_data(self, data):
        if self.script is not None:
            self.script += data
        elif self.ignored is None and self.anchor is not None:
            self.anchor[1] += data[:500 - len(self.anchor[1])]

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            if len(self.blocks) >= MAX_RECORDS:
                raise ValueError('too many JSON blocks')
            self.blocks.append(self.script)
            self.script = None
        if tag == self.ignored:
            self.ignored = None
        if tag == 'a' and self.anchor is not None:
            self.links.append(tuple(self.anchor))
            self.anchor = None


def _is_type(record, kind):
    value = record.get('@type')
    return value == kind or (isinstance(value, list) and kind in value)


def _tenant(url):
    parts = urlsplit(url)
    if parts.hostname not in ('jobs.lever.co', 'boards.greenhouse.io', 'job-boards.greenhouse.io'):
        return None
    bits = parts.path.split('/')
    if len(bits) < 2 or not re.fullmatch(r'[A-Za-z0-9_-]+', bits[1]):
        return None
    # Escapes/dot segments are not delegation evidence.
    if any(b in ('.', '..') for b in bits) or '%' in parts.path:
        return None
    return parts.hostname, bits[1]


def _delegation(candidate, links, base, hosts):
    """Exact literal link, or exact supported provider tenant. Never suffix trust."""
    for href, _ in links:
        link = _url(urljoin(base, href))
        if not link:
            continue
        if link == candidate:
            if _host(candidate) in hosts or _tenant(candidate):
                return href
        tenant = _tenant(link)
        if tenant and tenant == _tenant(candidate):
            return href
    return None


def _location(record):
    if record.get('jobLocationType') == 'TELECOMMUTE':
        restriction = record.get('applicantLocationRequirements')
        if isinstance(restriction, dict) and _text(restriction.get('name'), 120):
            return 'Remote ' + restriction['name']
        return ''  # Unknown remote restrictions cannot corroborate identity.
    loc = record.get('jobLocation')
    if isinstance(loc, dict) and isinstance(loc.get('address'), dict):
        address = loc['address']
        values = [address[k] for k in ('addressLocality', 'addressRegion', 'addressCountry') if k in address]
        if values and all(_text(v, 120) for v in values):
            return ', '.join(values)
    return ''


def _source_reference(record, job):
    """Return the bounded raw supporting field, never a normalized quotation."""
    return next((record[k] for k in ('sameAs', 'url', 'mainEntityOfPage')
                 if _text(record.get(k), 1000)
                 and _url(record[k]) == _url(job.original_url)), None)


def _core(record):
    core = {k: record.get(k) for k in ('title', 'hiringOrganization',
            'jobLocation', 'jobLocationType', 'applicantLocationRequirements',
            'identifier', 'datePosted', 'validThrough', 'url', 'sameAs', 'mainEntityOfPage')}
    org = record.get('hiringOrganization')
    core['hiringOrganization'] = _norm(org.get('name')) if isinstance(org, dict) else org
    core['title'] = _norm(record.get('title'))
    for field in ('url', 'sameAs', 'mainEntityOfPage'):
        core[field] = _url(record.get(field)) or record.get(field)
    for field in ('datePosted', 'validThrough'):
        parsed = _date(record.get(field))
        if parsed is not None:
            core[field] = parsed.isoformat()
    return json.dumps(core, sort_keys=True, ensure_ascii=True)


def _vacancy_keys(record, final):
    # An absent URL is scoped to the fetched listing, not the entire ATS board.
    keys = {('url', _url(record.get('url')) or final)}
    ident = record.get('identifier')
    if (isinstance(ident, dict) and _text(ident.get('name'), 160)
            and _text(ident.get('value'), 160)):
        keys.add(('requisition', ident['name'], ident['value']))
    return keys


def _conflicts(evidence):
    # All records are compared BEFORE company/title/location/activity filters.
    # Shared listing OR namespaced requisition links copies across fetched pages.
    seen = {}
    for record, final, _, _, _ in evidence:
        core = _core(record)
        for key in _vacancy_keys(record, final):
            if key in seen and seen[key] != core:
                return True
            seen[key] = core
    return False


def _match(record, job):
    org = record.get('hiringOrganization')
    if (not isinstance(org, dict) or not _text(org.get('name'), 160)
            or not _text(record.get('title'), 200)
            or _norm(org.get('name')) != _norm(job.company)
            or _norm(record.get('title')) != _norm(job.title)
            or _norm(_location(record)) != _norm(job.location)):
        return False
    if job.requisition_id is not None:
        ident = record.get('identifier')
        return (isinstance(ident, dict) and ident.get('name') == job.requisition_namespace
                and ident.get('value') == job.requisition_id)
    # Aggregator IDs are never employer requisitions. An exact source reference
    # in the matched JobPosting (not a generic page link) is strong corroboration.
    return _source_reference(record, job) is not None


def _activity(record, page, url, now):
    expiry = _date(record.get('validThrough'))
    posted = _date(record.get('datePosted'))
    if expiry is None or ('datePosted' in record and posted is None):
        return 'unverified'
    if posted is not None and (posted > now or posted > expiry):
        return 'unverified'
    if expiry <= now:
        return 'closed'
    return 'verified' if _apply_link(page, url) else 'unverified'


def _apply_link(page, url):
    for href, label in page.links:
        target = _url(urljoin(url, href))
        if (target and len(href) <= 1000 and _host(target) == _host(url)
                and urlsplit(target).path.rstrip('/') == urlsplit(url).path.rstrip('/') + '/apply'
                and re.search(r'\bapply\b', label, re.I)):
            return href
    return None


def _brief(page, fetched, company):
    values = {}
    for record in page.records:
        if not _is_type(record, 'Organization') or _norm(record.get('name')) != _norm(company):
            continue
        for field in ('legalName', 'description', 'sector', 'foundingDate'):
            value = record.get(field)
            if _text(value, 500) and '<' not in value and '>' not in value:
                values.setdefault(field, set()).add(value)
    claims = []
    for field, entries in values.items():
        if len(entries) == 1:
            value = next(iter(entries))
            claims.append(CompanyClaim(field, value, Citation(fetched.url, value, fetched.checked_at, 'company_reported')))
        if len(claims) == 3:
            break
    return CompanyBrief('available', tuple(claims)) if claims else CompanyBrief()


class OpportunityIntelligence:
    def __init__(self, search: SearchAdapter, fetcher: Fetcher, *, clock=None):
        self.search = search
        self.fetcher = fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = self.clock()
        self._opportunities = 0

    def _fresh(self, fetched):
        checked = _checked(fetched.checked_at)
        return checked is not None and self.started_at <= checked <= self.clock()

    def _page(self, fetched):
        if (fetched.status != 'ok' or not _url(fetched.url) or not self._fresh(fetched)
                or not isinstance(fetched.text, str)):
            return None
        try:
            return _Page(fetched.text, fetched.content_type)
        except (ValueError, UnicodeError, RecursionError):
            return None

    def resolve(self, job: PublicJobIdentity, context: TrustedEmployerContext | None = None,
                *, enabled=False, dry_run=False) -> OpportunityResult:
        source = job.original_url if type(job) is PublicJobIdentity else None
        def result(status, **fields):
            return OpportunityResult(status, source, **fields)
        if type(enabled) is not bool or type(dry_run) is not bool:
            return result('invalid_input')
        if not enabled:
            return result('disabled')
        if dry_run:
            return result('dry_run')
        if type(job) is not PublicJobIdentity or (context is not None and type(context) is not TrustedEmployerContext):
            return result('invalid_input')
        if context is not None and _norm(context.company) != _norm(job.company):
            return result('invalid_input')
        if self._opportunities >= 3:
            return result('budget_exhausted')
        self._opportunities += 1
        discovered = self.search.search(PublicSearchRequest(job.company, job.title, job.location))
        candidates = tuple(dict.fromkeys(u for lead in discovered.leads[:5] if (u := _url(lead.source_url))))
        common = dict(candidates=candidates, candidate_url=candidates[0] if candidates else None)
        if discovered.status != 'found':
            # Search not_found is not proof the employer vacancy does not exist.
            status = 'unverified' if discovered.status == 'not_found' else discovered.status
            return result(status, **common)
        if context is None or not candidates:
            return result('unverified', **common)
        careers = self.fetcher.fetch(context.careers_url)
        if careers.status != 'ok':
            return result('budget_exhausted' if careers.status == 'budget_exhausted' else 'fetch_failed', **common)
        page = self._page(careers)
        if page is None or _host(careers.url) not in context.hosts:
            return result('unverified', **common)
        brief = _brief(page, careers, job.company)
        common['company_brief'] = brief
        matches = {}
        evidence = []
        partial = None
        for candidate in candidates:
            delegation = _delegation(candidate, page.links, careers.url, context.hosts)
            if delegation is None or len(delegation) > 1000:
                continue
            fetched = self.fetcher.fetch(candidate)
            if fetched.status != 'ok':
                partial = 'budget_exhausted' if fetched.status == 'budget_exhausted' else (partial or 'fetch_failed')
                continue
            listing = self._page(fetched)
            final = _url(fetched.url)
            if listing is None or not final:
                partial = partial or 'unverified'
                continue
            # Redirects cannot create authority; a final URL must retain exact
            # tenant or employer host AND have its own careers delegation.
            if (_host(final) != _host(candidate) or _tenant(final) != _tenant(candidate)
                    or _delegation(final, page.links, careers.url, context.hosts) is None):
                continue
            evidence.extend((record, final, listing, fetched, delegation) for record in listing.records
                            if _is_type(record, 'JobPosting'))
        if _conflicts(evidence):
            return result('ambiguous', reason='conflicting_vacancy_evidence', **common)
        for record, final, listing, fetched, delegation in evidence:
            if not _match(record, job):
                continue
            record_url = _url(record.get('url'))
            if 'url' in record and record_url not in (final, _url(job.original_url)):
                continue
            # Optional metadata differences do not create a second vacancy.
            key = (final, _core(record))
            reference = _source_reference(record, job)
            if job.requisition_id is None and (reference is None or reference not in fetched.text):
                continue
            citations = (
                context.provenance,
                Citation(careers.url, delegation, careers.checked_at, 'ownership'),
                Citation(final, record['title'], fetched.checked_at, 'identity'),
                Citation(final, record['hiringOrganization']['name'], fetched.checked_at, 'identity'),
            )
            if record.get('jobLocationType') == 'TELECOMMUTE':
                location_values = ('TELECOMMUTE', record['applicantLocationRequirements']['name'])
            else:
                address = record['jobLocation']['address']
                location_values = tuple(address[k] for k in (
                    'addressLocality', 'addressRegion', 'addressCountry') if k in address)
            citations += tuple(Citation(final, value, fetched.checked_at, 'identity')
                               for value in location_values)
            if job.requisition_id is not None:
                citations += (Citation(final, job.requisition_namespace, fetched.checked_at, 'identity'),
                              Citation(final, job.requisition_id, fetched.checked_at, 'identity'))
            else:
                citations += (Citation(final, reference, fetched.checked_at, 'identity'),)
            if _text(record.get('validThrough'), 40):
                citations += (Citation(final, record['validThrough'], fetched.checked_at, 'activity'),)
            apply_link = _apply_link(listing, final)
            if apply_link and len(apply_link) <= 1000:
                citations += (Citation(final, apply_link, fetched.checked_at, 'activity'),)
            matches[key] = (final, _activity(record, listing, final, self.clock()), fetched.checked_at, citations)
        if len(matches) > 1:
            return result('ambiguous', reason='multiple_plausible_vacancies', **common)
        if partial:
            return result(partial, **common)
        if not matches:
            return result('unverified', **common)  # bounded search is never complete no-match proof
        final, status, checked, citations = next(iter(matches.values()))
        return result(status, official_url=final if status == 'verified' else None,
                      checked_at=checked, citations=citations, **common)
