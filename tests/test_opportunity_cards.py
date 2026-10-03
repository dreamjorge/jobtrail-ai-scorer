import json

import pytest

from jobtrail_ai_scorer.automation import AutomationConfig, AutomationRun, JobSearchAutomation, SimulationScenario
from jobtrail_ai_scorer.opportunity_intelligence import PublicJobIdentity, OpportunityResult




def test_public_cards_bound_and_separate_links():
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    result = OpportunityResult('unverified', job.original_url, official_url='https://acme.test/1', candidate_url='https://acme.test/1')
    cards = serialize_cards([(job, result)] * 5)
    assert len(cards) == 3
    assert 'official_url' not in cards[0]
    assert cards[0]['original_url'] == job.original_url
    assert 'Candidate (unverified)' in render_cards(cards)


@pytest.mark.parametrize('url', ['https://a.test/?token=secret', 'https://user:pass@a.test/', 'http://a.test/'])
def test_cards_reject_unsafe_proof(url):
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    card = serialize_cards([(job, OpportunityResult('verified', job.original_url, official_url=url))])[0]
    assert card['status'] != 'verified'
    assert 'official_url' not in card




@pytest.mark.parametrize('card', [
    {'company': 'Acme', 'title': 'Engineer', 'status': 'unverified'},
    {'company': 'Acme', 'title': 'Engineer', 'status': 'verified', 'original_url': 'https://source.test/1'},
])
def test_renderer_requires_original_and_consistent_verified_output(card):
    from jobtrail_ai_scorer.opportunity_cards import render_cards
    with pytest.raises(ValueError):
        render_cards([card])


@pytest.mark.parametrize('status', ['unverified', 'stale', 'closed', 'fetch_failed', 'unavailable'])
def test_renderer_never_promotes_failure(status):
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    cards = serialize_cards([(job, OpportunityResult(status, job.original_url,
        official_url='https://acme.test/1', candidate_url='https://acme.test/1'))])
    assert 'Official (verified)' not in render_cards(cards)
    assert cards[0]['original_url'] == job.original_url


def test_context_closed_schema_and_bounds():
    from jobtrail_ai_scorer.opportunity_intelligence import parse_employer_contexts
    context = {'company': 'Acme', 'hosts': ['acme.example'], 'careers_url': 'https://acme.example/careers',
               'provenance': {'url': 'https://acme.example/careers', 'excerpt': 'Operator assertion',
                              'checked_at': '2026-06-01T00:00:00+00:00', 'kind': 'user_confirmed'}}
    assert parse_employer_contexts(json.dumps([context]))[0].company == 'Acme'
    for payload in [[context] * 11, [dict(context, profile='RESUME_SENTINEL')], [context, context],
                    [dict(context, hosts=['jobs.lever.co'])]]:
        with pytest.raises(ValueError, match='^invalid employer contexts$'):
            parse_employer_contexts(json.dumps(payload))




def test_cards_omit_forbidden_claims_and_nested_payloads():
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    from jobtrail_ai_scorer.opportunity_intelligence import Citation, CompanyBrief, CompanyClaim
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    proof = Citation('https://acme.test/', 'PROFILE_SENTINEL', '2026-06-01T00:00:00+00:00', 'company_reported')
    cards = serialize_cards([(job, OpportunityResult('unverified', job.original_url,
        citations=(proof,), company_brief=CompanyBrief('available', (
            CompanyClaim('sector', 'RESUME_SENTINEL', proof),))))])
    assert cards[0]['citations'] == cards[0]['claims'] == []
    assert 'SENTINEL' not in render_cards(cards)
    cards[0]['claims'] = [{'value': {'profile': {'cv': 'RESUME_SENTINEL'}}}]
    with pytest.raises(ValueError):
        render_cards(cards)


_CREDENTIAL_TEXT_URLS = [
    'https://user:pass@public.example/?token=synthetic-secret',
    'https://public.example/?ToKeN=synthetic-secret',
    'https://public.example/?%61pi_key=synthetic-secret',
    'HTTPS://public.example/?%2541CCESS_TOKEN=synthetic-secret',
    'https%3A%2F%2Fuser%3Apass%40public.example%2F',
] + [f'https://public.example/?{key}=synthetic-secret' for key in (
    'apikey', 'password', 'client_secret', 'secret', 'app_id', 'app_key',
)]


@pytest.mark.parametrize('url', _CREDENTIAL_TEXT_URLS)
@pytest.mark.parametrize('field', ['claim', 'excerpt'])
def test_cards_omit_inline_credentials_without_rewriting_quotes(url, field):
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    from jobtrail_ai_scorer.opportunity_intelligence import Citation, CompanyBrief, CompanyClaim
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    unsafe = f'Company information at {url} is available.'
    proof = Citation('https://public.example/about', unsafe if field == 'excerpt' else 'Software company',
                     '2026-06-01T00:00:00+00:00', 'company_reported')
    claim = CompanyClaim('sector', unsafe if field == 'claim' else 'Software', proof)
    cards = serialize_cards([(job, OpportunityResult('unverified', job.original_url,
        citations=(proof,), company_brief=CompanyBrief('available', (claim,))))])
    assert cards[0]['claims'] == []
    assert cards[0]['citations'] == ([] if field == 'excerpt' else [{
        'url': proof.url, 'excerpt': proof.excerpt, 'retrieved_at': proof.checked_at}])
    assert url not in json.dumps(cards)
    assert url not in render_cards(cards)
    assert 'synthetic-secret' not in render_cards(cards)


@pytest.mark.parametrize('field', ['company', 'title', 'location'])
@pytest.mark.parametrize('url', _CREDENTIAL_TEXT_URLS)
def test_identity_text_rejects_embedded_credentials(field, url):
    from dataclasses import replace
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    with pytest.raises(ValueError, match='invalid public search field'):
        replace(job, **{field: f'Public text {url}'})


@pytest.mark.parametrize('field', ['company', 'title', 'claim', 'excerpt'])
@pytest.mark.parametrize('url', _CREDENTIAL_TEXT_URLS)
def test_renderer_revalidates_embedded_credentials(field, url):
    from jobtrail_ai_scorer.opportunity_cards import render_cards
    proof = {'url': 'https://public.example/about', 'excerpt': 'Software company',
             'retrieved_at': '2026-06-01T00:00:00+00:00'}
    claim = {'field': 'sector', 'value': 'Software', 'attribution': 'company_reported',
             'citation': proof}
    card = {'company': 'Acme', 'title': 'Engineer', 'status': 'unverified',
            'original_url': 'https://source.test/1', 'claims': [claim], 'citations': [proof]}
    unsafe = f'Company information at {url} is available.'
    if field in ('company', 'title'):
        card[field] = unsafe
    elif field == 'claim':
        claim['value'] = unsafe
    else:
        proof['excerpt'] = unsafe
    with pytest.raises(ValueError, match='invalid public card text'):
        render_cards([card])


@pytest.mark.parametrize('url', _CREDENTIAL_TEXT_URLS)
def test_status_text_cannot_disclose_credentials(url):
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    cards = serialize_cards([(job, OpportunityResult(f'Public status {url}', job.original_url))])
    assert cards[0]['status'] == 'unavailable'
    assert url not in json.dumps(cards)
    assert url not in render_cards(cards)
    cards[0]['status'] = f'Public status {url}'
    with pytest.raises(ValueError, match='invalid status'):
        render_cards(cards)


def test_cards_preserve_supported_quotes_with_safe_inline_public_urls():
    from jobtrail_ai_scorer.opportunity_cards import serialize_cards, render_cards
    from jobtrail_ai_scorer.opportunity_intelligence import Citation, CompanyBrief, CompanyClaim
    job = PublicJobIdentity('Acme', 'Engineer', 'Remote', 'https://source.test/1', 'indeed', '1')
    excerpt = 'Software company (https://public.example/about?lang=en).'
    proof = Citation('https://public.example/about', excerpt,
                     '2026-06-01T00:00:00+00:00', 'company_reported')
    value = 'Software: https://public.example/sector?lang=en'
    cards = serialize_cards([(job, OpportunityResult('unverified', job.original_url,
        citations=(proof,), company_brief=CompanyBrief('available', (
            CompanyClaim('sector', value, proof),))))])
    assert cards[0]['citations'][0]['excerpt'] == excerpt
    assert cards[0]['claims'][0]['citation']['excerpt'] == excerpt
    assert cards[0]['claims'][0]['value'] == value
    assert excerpt in render_cards(cards)
    assert value in render_cards(cards)
