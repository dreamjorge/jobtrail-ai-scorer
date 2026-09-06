import json

import pytest

import jobtrail_ai_scorer.automation as automation
from jobtrail_ai_scorer.automation import (
    AutomationConfig,
    BackendDiscoveryError,
    JobSearchAutomation,
    JobTrailHTTPClient,
    DISCOVER_DEFAULT_CONTAINER,
    map_jobspy_job,
    build_notification_summary,
    merge_resolved_base_url,
    parse_score_note,
    resolve_automation_base_url,
    search_payloads,
)
from jobtrail_ai_scorer.seen_cache import SeenCache


class FakeJobTrail:
    def __init__(self):
        self.searches = []
        self.imported = []
        self.jobs = {}

    def search(self, payload):
        self.searches.append(payload)
        return [
            {
                "site": "indeed",
                "id": "source-1",
                "title": "Python Engineer",
                "company": "Acme",
                "description": "good",
                "job_url": "https://jobs.test/1",
                "location": "Queretaro",
                "is_remote": False,
            }
        ]

    def import_job(self, payload):
        self.imported.append(payload)
        self.jobs["j1"] = {**payload, "id": "j1", "notes": []}
        return {"id": "j1"}

    def get_job(self, job_id):
        return self.jobs[job_id]


class FakeScorer:
    def __init__(self):
        self.calls = []

    def __call__(self, job_id, config_path):
        self.calls.append((job_id, config_path))
        self.jobs[job_id]["notes"] = [
            {
                "body": "[AI_JOB_SCORE_V1]\n"
                + json.dumps(
                    {
                        "score": 91,
                        "recommendation": "PRIORITY_APPLY",
                        "strengths": ["Python"],
                        "gaps": ["None"],
                    }
                )
            }
        ]


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(message)


def test_search_payloads_split_sites_and_locations():
    config = AutomationConfig.from_env({})
    payloads = search_payloads(config)
    assert payloads == [
        {
            "sites": ["linkedin", "indeed"],
            "searchTerm": config.search_terms,
            "location": "Queretaro",
            "resultsWanted": 10,
            "hoursOld": 72,
            "isRemote": False,
        },
        {
            "sites": ["linkedin", "indeed"],
            "searchTerm": config.search_terms,
            "location": "remote",
            "resultsWanted": 10,
            "hoursOld": 72,
            "isRemote": True,
        },
    ]


def test_import_mapping_omits_nulls_and_maps_jobspy_fields():
    assert map_jobspy_job(
        {
            "site": "indeed",
            "id": "x",
            "company": "C",
            "title": "T",
            "description": "D",
            "job_url": "U",
            "location": "Q",
            "is_remote": True,
            "min_amount": 10,
            "max_amount": 20,
            "currency": "USD",
            "job_type": "fulltime",
        }
    ) == {
        "source": "indeed",
        "sourceJobId": "x",
        "company": "C",
        "position": "T",
        "description": "D",
        "jobUrl": "U",
        "location": "Q",
        "remote": True,
        "salaryMin": 10,
        "salaryMax": 20,
        "salaryCurrency": "USD",
        "jobType": "fulltime",
    }


def test_import_mapping_omits_none_jobspy_fields():
    assert map_jobspy_job(
        {
            "site": "indeed",
            "id": "x",
            "is_remote": None,
            "min_amount": None,
            "max_amount": None,
            "currency": None,
            "job_type": None,
        }
    ) == {"source": "indeed", "sourceJobId": "x"}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class RecordingHTTPClient:
    def __init__(self):
        self.posts = []

    def post(self, path, *, json):
        self.posts.append((path, json))
        return FakeResponse([] if path.endswith("search") else {"id": "j1"})


class SearchResponseHTTPClient(RecordingHTTPClient):
    def post(self, path, *, json):
        self.posts.append((path, json))
        return FakeResponse({"results": [{"id": "x"}], "cached": False})


def test_http_client_search_returns_results_from_discover_response():
    client = JobTrailHTTPClient("http://jobtrail", client=SearchResponseHTTPClient())

    assert client.search({"site": "indeed"}) == [{"id": "x"}]


def test_http_client_uses_discover_routes_for_search_and_import():
    recorder = RecordingHTTPClient()
    client = JobTrailHTTPClient("http://jobtrail", client=recorder)
    payload = {"site": "indeed"}
    client.search(payload)
    client.import_job(payload)
    assert recorder.posts == [
        ("/api/discover/search", payload),
        ("/api/discover/import", payload),
    ]


def test_http_client_url_encodes_job_id():
    class GetRecordingHTTPClient:
        def __init__(self):
            self.path = None

        def get(self, path):
            self.path = path
            return FakeResponse({"id": "j/1"})

    recorder = GetRecordingHTTPClient()
    client = JobTrailHTTPClient("http://jobtrail", client=recorder)

    assert client.get_job("j/1") == {"id": "j/1"}
    assert recorder.path == "/api/jobs/j%2F1"


def test_run_scores_real_mode_and_notifies_once_for_best_match():
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()
    result = JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", notify_enabled=True
        )
    )
    assert scorer.calls == [("j1", "safe/config.yaml")]
    assert len(notifier.messages) == 1
    assert result.selected["score"] == 91
    assert "good" not in notifier.messages[0]
    assert "candidate" not in notifier.messages[0].lower()


def test_selected_notification_contains_job_and_score_data(monkeypatch):
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()
    original_summary = automation.build_notification_summary
    summary_args = []

    def capture_summary(job, score):
        summary_args.append((job, score))
        return original_summary(job, score)

    monkeypatch.setattr(automation, "build_notification_summary", capture_summary)
    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", notify_enabled=True
        )
    )

    assert summary_args[0][0] is gateway.jobs["j1"]
    assert summary_args[0][1]["score"] == 91
    message = json.loads(notifier.messages[0])
    assert message == {
        "title": "Python Engineer",
        "company": "Acme",
        "location": "Queretaro",
        "jobUrl": "https://jobs.test/1",
        "score": 91,
        "recommendation": "PRIORITY_APPLY",
        "strengths": ["Python"],
        "gaps": ["None"],
    }


def test_no_notification_when_score_below_threshold():
    gateway = FakeJobTrail()

    def score(job_id, config_path):
        gateway.jobs[job_id]["notes"] = [
            {
                "body": '[AI_JOB_SCORE_V1]\n{"score":79,"recommendation":"REVIEW","strengths":[],"gaps":[]}'
            }
        ]

    notifier = FakeNotifier()
    result = JobSearchAutomation(gateway, scorer=score, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", notify_enabled=True
        )
    )
    assert result.selected is None
    assert notifier.messages == []


def test_parse_score_note_is_safe_and_uses_latest_valid_marker():
    notes = [
        {"body": "[AI_JOB_SCORE_V1]\nnot-json"},
        {
            "body": '[AI_JOB_SCORE_V1]\n{"score":88,"recommendation":"APPLY","strengths":["a"],"gaps":["b"]}'
        },
    ]
    assert parse_score_note(notes)["score"] == 88


def test_default_scorer_preserves_direct_cli_job_id_invocation(monkeypatch):
    gateway = FakeJobTrail()
    calls = []

    def run(args, *, check):
        calls.append((args, check))
        gateway.jobs["j1"]["notes"] = [{"body": '[AI_JOB_SCORE_V1]\\n{"score":81}'}]

    monkeypatch.setattr("jobtrail_ai_scorer.automation.subprocess.run", run)
    result = JobSearchAutomation(gateway).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", scorer_command="jobtrail-ai-scorer"
        )
    )

    assert calls == [
        (
            [
                "jobtrail-ai-scorer",
                "score",
                "--config",
                "safe/config.yaml",
                "--job-id",
                "j1",
                "--force",
            ],
            True,
        )
    ]
    assert result.scored == 1


def test_failed_scorer_cannot_select_or_notify_old_high_marker():
    gateway = FakeJobTrail()
    notifier = FakeNotifier()

    def score(job_id, _config_path):
        gateway.jobs[job_id]["notes"] = [
            {
                "body": '[AI_JOB_SCORE_V1]\\n{"score":99,"recommendation":"PRIORITY_APPLY"}',
            }
        ]
        raise RuntimeError("scorer failed")

    result = JobSearchAutomation(gateway, scorer=score, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", notify_enabled=True
        )
    )

    assert result.selected is None
    assert notifier.messages == []


def test_notification_summary_allowlist_and_bounded_text():
    summary = build_notification_summary(
        {
            "position": "Visible title",
            "company": "Visible company",
            "location": "Visible location",
            "jobUrl": "https://jobs.test/1",
            "description": "FORBIDDEN DESCRIPTION",
            "notes": "FORBIDDEN NOTES",
            "candidate_profile": "FORBIDDEN PROFILE",
            "prompt": "FORBIDDEN PROMPT",
        },
        {
            "score": 88,
            "recommendation": "APPLY",
            "strengths": ["S" * 300],
            "gaps": ["G" * 300],
            "reasoning": "FORBIDDEN REASONING",
        },
    )

    assert set(summary) == {
        "title",
        "company",
        "location",
        "score",
        "recommendation",
        "strengths",
        "gaps",
        "jobUrl",
    }
    assert "FORBIDDEN" not in json.dumps(summary)
    assert len(summary["strengths"][0]) <= 200
    assert len(summary["gaps"][0]) <= 200


class _FakeProbe:
    def __init__(self, results):
        self.calls = []
        self._results = results

    def __call__(self, url):
        self.calls.append(url)
        return self._results.get(url, False)


class _FakeInspect:
    def __init__(self, outputs):
        self.calls = []
        self._outputs = outputs

    def __call__(self, name):
        self.calls.append(name)
        return self._outputs.get(name)


def test_resolve_automation_base_url_falls_back_to_static_when_discovery_disabled():
    config = AutomationConfig(base_url="http://example:8000", discover_container=None)
    probe = _FakeProbe({})
    inspect = _FakeInspect({})
    url, source = resolve_automation_base_url(config, probe=probe, inspect=inspect)
    assert url == "http://example:8000"
    assert source == "static"
    assert probe.calls == [] and inspect.calls == []


def test_resolve_automation_base_url_uses_discovery_when_container_is_set():
    config = AutomationConfig(
        base_url="http://ignored:8000", discover_container=DISCOVER_DEFAULT_CONTAINER
    )
    probe = _FakeProbe({"http://127.0.0.1:8000": True})
    inspect = _FakeInspect({})
    url, source = resolve_automation_base_url(config, probe=probe, inspect=inspect)
    assert url == "http://127.0.0.1:8000"
    assert source == "published-port"


def test_resolve_automation_base_url_propagates_backend_discovery_error():
    config = AutomationConfig(
        base_url="http://ignored:8000", discover_container="missing-container"
    )
    probe = _FakeProbe({})
    inspect = _FakeInspect({"missing-container": ""})
    with pytest.raises(BackendDiscoveryError):
        resolve_automation_base_url(config, probe=probe, inspect=inspect)


def test_merge_resolved_base_url_replaces_url_only():
    config = AutomationConfig(base_url="http://old:8000", scorer_config_path="cfg.yaml")
    patched = merge_resolved_base_url(config, "http://new:9999")
    assert patched.base_url == "http://new:9999"
    assert patched.scorer_config_path == "cfg.yaml"
    assert config.base_url == "http://old:8000"


def test_automation_from_env_reads_discover_container():
    env = {"JOBTRAIL_DISCOVER_CONTAINER": "backend-staging"}
    config = AutomationConfig.from_env(env)
    assert config.discover_container == "backend-staging"


def test_automation_from_env_defaults_discover_container_to_none():
    config = AutomationConfig.from_env({})
    assert config.discover_container is None


# --- Seen-cache integration --------------------------------------------------


class _CacheClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_automation_skips_offers_already_in_seen_cache(tmp_path):
    cache = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_000.0))
    # Pre-seed the cache with the offer FakeJobTrail will return.
    cache.mark_seen("indeed", "source-1")
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(gateway, scorer=scorer, seen_cache=cache).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            hours_old=72,
            results_wanted=10,
            locations=("Queretaro",),
        )
    )

    assert gateway.imported == []
    assert result.imported == 0
    assert result.scored == 0
    assert result.searched == 1  # search still runs; cache filters at import time


def test_automation_records_imported_offers_in_seen_cache(tmp_path):
    cache = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_000.0))
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(gateway, scorer=scorer, seen_cache=cache).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml", hours_old=72)
    )

    assert result.imported == 1
    # Cache now contains the (source, sourceJobId) pair.
    assert cache.should_skip("indeed", "source-1", hours_old=72) is True
    # Reloading from disk confirms the entry was persisted.
    reloaded = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_010.0))
    assert reloaded.should_skip("indeed", "source-1", hours_old=72) is True


def test_automation_continues_run_when_seen_cache_load_fails(tmp_path, monkeypatch):
    """A broken cache file must never crash the automation run."""

    cache_path = tmp_path / "seen.json"
    cache_path.write_text("{not json", encoding="utf-8")
    cache = SeenCache(cache_path, clock=_CacheClock(1_000.0))
    assert cache.size == 0  # degraded to empty

    # Force mark_seen to fail and ensure the run still completes.
    def _boom_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cache, "save", _boom_save)

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(gateway, scorer=scorer, seen_cache=cache).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            hours_old=72,
            locations=("Queretaro",),
        )
    )

    # Import still happened (search was not blocked), and the run reported
    # a seen-cache failure without aborting.
    assert result.searched == 1
    assert any(failure.startswith("seen-cache:") for failure in result.failures)


def test_automation_without_seen_cache_keeps_legacy_behavior():
    """The seen_cache argument must be optional to preserve existing callers."""

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(gateway, scorer=scorer).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml")
    )

    assert result.imported == 1
    assert result.scored == 1


def test_automation_seen_cache_uses_hours_old_for_ttl(tmp_path):
    """The TTL passed to the cache must follow the AutomationConfig.hours_old value."""

    clock = _CacheClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    JobSearchAutomation(gateway, scorer=scorer, seen_cache=cache).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml", hours_old=24)
    )

    # At the same instant, hours_old=24 (TTL=48h) keeps it skipped.
    assert cache.should_skip("indeed", "source-1", hours_old=24) is True
    # hours_old=72 (TTL=144h) also keeps it skipped at the same instant.
    assert cache.should_skip("indeed", "source-1", hours_old=72) is True

    # Move past the 48h TTL: hours_old=24 must report a miss while 72 reports a hit.
    clock.t = 1_000.0 + 49 * 3600
    assert cache.should_skip("indeed", "source-1", hours_old=24) is False
    assert cache.should_skip("indeed", "source-1", hours_old=72) is True
