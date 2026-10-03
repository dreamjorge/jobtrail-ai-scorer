"""Bounded public presentation, not a second vacancy-verification policy."""
from copy import deepcopy
from itertools import islice
import re
from urllib.parse import unquote

from .opportunity_intelligence import (
    Citation, CompanyBrief, CompanyClaim, OpportunityResult, PublicJobIdentity, _checked,
)
from .public_search import _candidate_url, _forbidden

_STATUSES = frozenset({
    'verified', 'unverified', 'closed', 'ambiguous', 'fetch_failed', 'budget_exhausted',
    'provider_unconfigured', 'provider_error', 'quota_exceeded', 'timeout', 'unavailable',
    'bad_response', 'oversized_response', 'untrusted', 'invalid_input',
    'invalid_configuration', 'disabled', 'dry_run', 'stale',
})


def _unsafe_text(value):
    # Inspect every decoding layer without changing the displayed text. Applying
    # the URL-field policy to embedded links also catches encoded credential keys
    # and userinfo. Never scrub a citation into a purported exact quote.
    while True:
        urls = [url.rstrip('.,;!)]}') for url in
                re.findall(r'''https?://[^\s<>"']+''', value, re.IGNORECASE)]
        if _forbidden(value) or any(
            not _candidate_url(url)
            or not _candidate_url(url.replace('#', '&' if '?' in url else '?', 1))
            for url in urls
        ):
            return True
        decoded = unquote(value)
        if decoded == value:
            return False
        value = decoded


def _text(value, limit):
    if (type(value) is not str or not value.strip() or len(value) > limit
            or _unsafe_text(value) or any(ord(c) < 32 for c in value)
            or '<' in value or '>' in value):
        raise ValueError('invalid public card text')
    return value


def _card_url(value):
    url = _candidate_url(value)
    return url if url and not _unsafe_text(value) else None


def _citation(proof):
    if type(proof) is not Citation:
        raise ValueError('invalid citation')
    url = _card_url(proof.url)
    if not url or not _checked(proof.checked_at):
        raise ValueError('invalid citation')
    return {'url': url, 'excerpt': _text(proof.excerpt, 1000),
            'retrieved_at': proof.checked_at}


def serialize_cards(pairs):
    """Only typed public projections/results. Never serialize arbitrary job data."""
    cards = []
    for job, result in islice(pairs, 3):
        if type(job) is not PublicJobIdentity or type(result) is not OpportunityResult:
            raise ValueError('expected public typed results')
        original = _card_url(job.original_url)
        if not original:
            raise ValueError('invalid original URL')
        card = {'company': _text(job.company, 160), 'title': _text(job.title, 200),
                'original_url': original,
                'status': result.status if result.status in _STATUSES else 'unavailable'}
        official = _card_url(result.official_url)
        # Trust T4's status, not candidate hosts/title heuristics. Invalid output
        # may only downgrade a label, never upgrade verification.
        if card['status'] == 'verified':
            if official and _checked(result.checked_at):
                card['official_url'] = official
            else:
                card['status'] = 'unavailable'
        candidate = _card_url(result.candidate_url)
        if candidate and 'official_url' not in card:
            card['candidate_url'] = candidate
        card['citations'] = []
        if type(result.citations) is tuple:
            for proof in result.citations[:3]:
                try:
                    card['citations'].append(_citation(proof))
                except ValueError:
                    continue
        card['claims'] = []
        brief = result.company_brief
        if type(brief) is CompanyBrief and type(brief.claims) is tuple:
            for claim in brief.claims[:3]:
                try:
                    if type(claim) is not CompanyClaim or claim.attribution != 'company_reported':
                        continue
                    # Deliberately do not expose description/CV-like free-form fields.
                    if claim.field not in ('legalName', 'sector', 'foundingDate'):
                        continue
                    card['claims'].append({'field': claim.field, 'value': _text(claim.value, 500),
                                           'attribution': 'company_reported',
                                           'citation': _citation(claim.citation)})
                except ValueError:
                    continue
        cards.append(card)
    return cards


def validated_cards(cards):
    """Deny unsafe caller mappings; share the typed serializer's public policy.

    Unlike typed serialization (which drops unsafe proofs), external mappings
    must already be complete, bounded public cards. Quotes are never rewritten.
    """
    if type(cards) is not list or not 1 <= len(cards) <= 3:
        raise ValueError('invalid public cards')
    for card in cards:
        if (type(card) is not dict or not {
                'company', 'title', 'original_url', 'status', 'citations', 'claims'
                } <= set(card)):
            raise ValueError('incomplete public card')
        for key in ('citations', 'claims'):
            if type(card[key]) is not list or len(card[key]) > 3:
                raise ValueError('invalid public card bounds')
        # Rendering validates every allowed field, nested proof, claim, URL,
        # date and status with the same policy used by typed serialization.
        render_cards([card])
    return deepcopy(cards)


def render_cards(cards):
    """Validate the closed, shallow serialized shape again before rendering."""
    lines = ['Public information (public sources only)']
    for card in islice(cards, 3):
        if type(card) is not dict or set(card) - {
            'company', 'title', 'original_url', 'status', 'official_url',
            'candidate_url', 'citations', 'claims',
        }:
            raise ValueError('invalid public card')
        status = card.get('status')
        if ('original_url' not in card or (status == 'verified' and 'official_url' not in card)
                or any(type(card.get(key, [])) is not list for key in ('citations', 'claims'))):
            raise ValueError('incomplete public card')
        if status not in _STATUSES:
            raise ValueError('invalid status')
        lines.append(f"{_text(card['company'], 160)} — {_text(card['title'], 200)}: {status.replace('_', ' ')}")
        for key, label in (('original_url', 'Original'), ('official_url', 'Official (verified)'),
                           ('candidate_url', 'Candidate (unverified)')):
            if key not in card:
                continue
            url = _card_url(card[key])
            if not url or (key == 'official_url' and status != 'verified'):
                raise ValueError('invalid public URL')
            lines.append(f'{label}: {url}')
        def proof_text(proof):
            if type(proof) is not dict or set(proof) != {'url', 'excerpt', 'retrieved_at'}:
                raise ValueError('invalid proof shape')
            url = _card_url(proof['url'])
            if not url or not _checked(proof['retrieved_at']):
                raise ValueError('invalid proof')
            return f"{_text(proof['excerpt'], 1000)} — {url} (retrieved {proof['retrieved_at']})"
        for proof in card.get('citations', [])[:3]:
            lines.append('Citation: ' + proof_text(proof))
        for claim in card.get('claims', [])[:3]:
            if (type(claim) is not dict or set(claim) != {'field', 'value', 'attribution', 'citation'}
                    or claim['field'] not in ('legalName', 'sector', 'foundingDate')
                    or claim['attribution'] != 'company_reported'):
                raise ValueError('invalid public claim')
            lines.append(f"Company reported {claim['field']}: {_text(claim['value'], 500)}; " + proof_text(claim['citation']))
    if len(lines) == 1:
        lines.append('unavailable')
    return '\n'.join(lines)
