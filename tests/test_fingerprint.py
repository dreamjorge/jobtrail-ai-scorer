import json

from jobtrail_ai_scorer.scoring import (
    FINGERPRINT_VERSION,
    CURRENT_MARKER,
    job_fingerprint,
    parse_score_note,
    serialize_score_note,
)


BASE_JOB = {
    "title": "Senior Python Engineer",
    "company": "Example Co",
    "location": "Mexico City",
    "description": "Build reliable APIs",
    "salaryMin": 100,
    "salaryMax": 150,
    "salaryCurrency": "USD",
    "jobType": "Full time",
    "remote": True,
}


def test_fingerprint_normalizes_unicode_case_and_whitespace():
    equivalent = {
        **BASE_JOB,
        "title": "  SENIOR\u00a0Python   Engineer ",
        "company": "Example CO",
        "description": "Build\nreliable\tAPIs",
    }
    assert job_fingerprint(BASE_JOB) == job_fingerprint(equivalent)


def test_fingerprint_is_insensitive_to_key_order():
    reordered = dict(reversed(list(BASE_JOB.items())))
    assert job_fingerprint(BASE_JOB) == job_fingerprint(reordered)


def test_meaningful_field_changes_change_digest():
    changed = {**BASE_JOB, "salaryMax": 151}
    assert job_fingerprint(BASE_JOB) != job_fingerprint(changed)


def test_excluded_fields_do_not_change_digest():
    changed = {
        **BASE_JOB,
        "id": "different",
        "url": "https://other.example/job",
        "metadata": {"retrieved_at": "later"},
        "notes": [{"body": "private note"}],
        "profile": "private profile",
    }
    assert job_fingerprint(BASE_JOB) == job_fingerprint(changed)


def test_digest_is_sha256_and_versioned_canonically():
    digest = job_fingerprint(BASE_JOB)
    assert len(digest) == 64
    int(digest, 16)
    assert FINGERPRINT_VERSION == 1
    assert digest == job_fingerprint(dict(BASE_JOB))


def test_score_note_round_trip_includes_fingerprint_when_job_available():
    note = serialize_score_note(CURRENT_MARKER, {"score": 80}, job=BASE_JOB)
    payload = parse_score_note(note)
    assert payload == {
        "score": 80,
        "fingerprint_version": 1,
        "input_fingerprint": job_fingerprint(BASE_JOB),
    }


def test_score_note_parser_accepts_legacy_payload():
    note = CURRENT_MARKER + "\n" + json.dumps({"score": 80})
    assert parse_score_note(note) == {"score": 80}
