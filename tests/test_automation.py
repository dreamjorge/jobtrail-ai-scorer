import json
import re

import httpx
import pytest

import jobtrail_ai_scorer.automation as automation
from jobtrail_ai_scorer.automation import (
    AtsBoardConfig,
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
    source_search_requests,
    ats_source_search_requests,
)
from jobtrail_ai_scorer.seen_cache import SeenCache
from jobtrail_ai_scorer.sources import JobSpySourceAdapter, NormalizedJob


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


def test_automation_config_parses_search_profiles_from_env():
    config = AutomationConfig.from_env(
        {
            "JOB_SEARCH_PROFILES": json.dumps(
                [
                    {
                        "name": "python",
                        "search_terms": "python backend",
                        "sites": ["linkedin"],
                        "locations": ["remote"],
                        "results_wanted": 5,
                        "hours_old": 24,
                    }
                ]
            )
        }
    )

    assert config.search_profiles == (
        automation.SearchProfile(
            name="python",
            search_terms="python backend",
            sites=("linkedin",),
            locations=("remote",),
            results_wanted=5,
            hours_old=24,
        ),
    )


@pytest.mark.parametrize(
    "raw_profiles",
    [
        "not-json",
        json.dumps({"name": "python"}),
        json.dumps(["python"]),
        json.dumps([{"name": "   "}]),
        json.dumps([{"name": "python"}, {"name": "python"}]),
        json.dumps([{"name": "python", "unexpected": True}]),
        json.dumps([{"name": "python", "sites": [None]}]),
        json.dumps([{"name": "python", "sites": [123]}]),
        json.dumps([{"name": "python", "locations": [{"city": "remote"}]}]),
    ],
    ids=[
        "invalid-json",
        "non-list",
        "non-object",
        "blank-name",
        "duplicate",
        "unknown-key",
        "null-site",
        "numeric-site",
        "object-location",
    ],
)
def test_automation_config_rejects_invalid_search_profiles(raw_profiles):
    with pytest.raises(ValueError):
        AutomationConfig.from_env({"JOB_SEARCH_PROFILES": raw_profiles})


def test_source_search_requests_expand_profiles_in_deterministic_order():
    config = AutomationConfig(
        sites=("indeed",),
        search_terms="global terms",
        locations=("Queretaro", "remote"),
        results_wanted=10,
        hours_old=72,
        search_profiles=(
            automation.SearchProfile(
                name="python",
                search_terms="python backend",
                sites=("linkedin",),
                locations=("remote", "Monterrey"),
                results_wanted=5,
            ),
            automation.SearchProfile(name="fallback"),
        ),
    )

    requests = source_search_requests(config)

    assert [request.profile_name for request in requests] == [
        "python",
        "python",
        "fallback",
        "fallback",
    ]
    assert [request.location for request in requests] == [
        "remote",
        "Monterrey",
        "Queretaro",
        "remote",
    ]
    assert [(request.search_term, request.sites) for request in requests] == [
        ("python backend", ("linkedin",)),
        ("python backend", ("linkedin",)),
        ("global terms", ("indeed",)),
        ("global terms", ("indeed",)),
    ]
    assert [request.results_wanted for request in requests] == [5, 5, 10, 10]
    assert [request.hours_old for request in requests] == [72, 72, 72, 72]
    assert [request.is_remote for request in requests] == [True, False, False, True]


def test_ats_source_search_requests_use_one_profile_request_and_ats_cap():
    config = AutomationConfig(
        locations=("remote", "Toronto"),
        results_wanted=3,
        ats_boards=AtsBoardConfig(
            greenhouse_boards=("acme",), results_wanted=17
        ),
        search_profiles=(
            automation.SearchProfile(
                name="python", locations=("remote", "Toronto")
            ),
            automation.SearchProfile(name="backend"),
        ),
    )

    requests = ats_source_search_requests(config)

    assert [request.profile_name for request in requests] == ["python", "backend"]
    assert [request.location for request in requests] == ["remote", "remote"]
    assert [request.results_wanted for request in requests] == [17, 17]


def test_search_payloads_preserves_legacy_default_without_profiles():
    config = AutomationConfig.from_env({})

    assert search_payloads(config) == [
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


def test_automation_can_import_normalized_jobs_from_source_adapter():
    class FakeSourceAdapter:
        name = "custom"

        def __init__(self):
            self.requests = []

        def search(self, request):
            self.requests.append(request)
            return [
                NormalizedJob(
                    source="custom",
                    source_job_id="custom-1",
                    title="Adapter Engineer",
                    company="Adapter Co",
                    description="adapter job",
                    source_url="https://jobs.test/custom-1",
                    location=request.location,
                    remote=request.is_remote,
                    search_profile=request.profile_name,
                )
            ]

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    adapter = FakeSourceAdapter()

    result = JobSearchAutomation(
        gateway, scorer=scorer, source_adapters=(adapter,)
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
            sites=("custom-site",),
        )
    )

    assert gateway.searches == []
    assert len(adapter.requests) == 1
    assert adapter.requests[0].sites == ("custom-site",)
    assert adapter.requests[0].location == "remote"
    assert adapter.requests[0].is_remote is True
    assert gateway.imported == [
        {
            "source": "custom",
            "sourceJobId": "custom-1",
            "company": "Adapter Co",
            "position": "Adapter Engineer",
            "description": "adapter job",
            "jobUrl": "https://jobs.test/custom-1",
            "location": "remote",
            "remote": True,
            "searchProfile": "default",
        }
    ]
    assert scorer.calls == [("j1", "safe/config.yaml")]
    assert result.searched == 1
    assert result.imported == 1
    assert result.scored == 1
    assert result.failures == ()


def test_automation_seen_cache_uses_normalized_job_identity(tmp_path):
    class PayloadAliasJob(NormalizedJob):
        def to_import_payload(self):
            payload = super().to_import_payload()
            payload["sourceJobId"] = "payload-alias"
            return payload

    class FakeSourceAdapter:
        name = "custom"

        def search(self, request):
            return [
                PayloadAliasJob(
                    source="custom",
                    source_job_id="canonical-1",
                    title="Adapter Engineer",
                )
            ]

    cache = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_000.0))
    cache.mark_seen("custom", "canonical-1")
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        seen_cache=cache,
        source_adapters=(FakeSourceAdapter(),),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
        )
    )

    assert gateway.imported == []
    assert result.searched == 1
    assert result.imported == 0
    assert result.scored == 0
    assert result.failures == ()


def test_automation_source_adapter_failure_is_partial():
    class PartiallyFailingSourceAdapter:
        name = "partial"

        def search(self, request):
            if request.location == "Queretaro":
                raise RuntimeError("provider unavailable")
            return [
                NormalizedJob(
                    source="partial",
                    source_job_id="partial-1",
                    title="Recovered Engineer",
                    location=request.location,
                )
            ]

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        source_adapters=(PartiallyFailingSourceAdapter(),),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("Queretaro", "remote"),
        )
    )

    assert gateway.imported == [
        {
            "source": "partial",
            "sourceJobId": "partial-1",
            "position": "Recovered Engineer",
            "location": "remote",
        }
    ]
    assert result.searched == 1
    assert result.imported == 1
    assert result.scored == 1
    assert result.failures == ("search:terminal:RuntimeError",)


def test_automation_run_profile_counts_defaults_empty():
    assert automation.AutomationRun().profile_counts == {}


def test_automation_default_source_adapters_unchanged_when_no_ats_boards():
    """When JOB_ATS_BOARDS is unset, the default ``source_adapters`` tuple
    must remain exactly ``(JobSpySourceAdapter(gateway),)`` — the plumbing
    change in PR-A must not silently add or remove adapters when no ATS
    boards are configured.
    """

    from jobtrail_ai_scorer.sources import JobSpySourceAdapter

    gateway = FakeJobTrail()
    instance = JobSearchAutomation(gateway)

    # ``JobSpySourceAdapter`` does not implement ``__eq__``; assert on shape
    # and wiring instead of object identity.
    adapters = instance.source_adapters
    assert len(adapters) == 1
    assert isinstance(adapters[0], JobSpySourceAdapter)
    assert adapters[0].gateway is gateway
    assert adapters[0].name == "jobspy"


def test_automation_adzuna_adapter_exception_does_not_block_jobspy_import():
    """An ``AdzunaSourceAdapter`` that raises during ``search`` must be
    caught at the orchestrator as a ``search:`` failure while the
    JobSpy adapter keeps importing its results and the run selects a
    best match."""

    import httpx as _httpx

    from jobtrail_ai_scorer.sources import JobSpySourceAdapter
    from jobtrail_ai_scorer.sources.adzuna import (
        AdzunaConfig,
        AdzunaHttpError,
        AdzunaSourceAdapter,
    )

    class RaisingAdzunaAdapter(AdzunaSourceAdapter):
        """AdzunaSourceAdapter subclass that always raises from search.

        Subclassing (rather than wrapping) keeps the production
        constructor wired exactly as the orchestrator expects, so the
        failure path exercises the real ``search()`` raise site.
        """

        def __init__(self) -> None:
            config = AdzunaConfig(
                app_id="id-123",
                app_key="key-456",
                country="us",
            )
            super().__init__(
                config=config,
                client=_httpx.Client(
                    transport=_httpx.MockTransport(
                        lambda request: _httpx.Response(200, request=request)
                    )
                ),
            )

        def search(self, request):  # type: ignore[override]
            raise AdzunaHttpError(
                "adzuna rejected the request: status=403",
                status_code=403,
            )

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    adzuna_adapter = RaisingAdzunaAdapter()

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        # Pass *both* adapters: the production code defaults to JobSpy
        # when ``source_adapters`` is None, so the test must opt in
        # explicitly to keep JobSpy in the mix alongside the failing
        # Adzuna adapter.
        source_adapters=(JobSpySourceAdapter(gateway), adzuna_adapter),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("Queretaro",),
        )
    )

    # JobSpy results were still imported via the default JobSpy
    # adapter, so the run has a selected match above the threshold.
    assert result.searched == 1
    assert result.imported == 1
    assert result.scored == 1
    assert result.selected is not None
    assert result.selected["title"] == "Python Engineer"
    assert gateway.imported == [
        {
            "source": "indeed",
            "sourceJobId": "source-1",
            "company": "Acme",
            "position": "Python Engineer",
            "description": "good",
            "jobUrl": "https://jobs.test/1",
            "location": "Queretaro",
            "remote": False,
            "searchProfile": "default",
        }
    ]

    # The Adzuna failure is recorded as a ``search:`` failure with the
    # terminal classification (4xx is non-retryable) and the exception
    # type so operators can grep for it. The credentialed URL must
    # not appear in the label.
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.startswith("search:terminal")
    assert "AdzunaHttpError" in failure
    assert "key-456" not in failure
    assert "id-123" not in failure


def test_automation_deduplicates_imports_across_profiles_and_counts_provenance():
    class ProfileDedupeAdapter:
        name = "profiles"

        def __init__(self):
            self.requests = []

        def search(self, request):
            self.requests.append(request)
            if request.profile_name == "python":
                return [
                    NormalizedJob(
                        source="Indeed",
                        source_job_id=" SHARED-1 ",
                        title="Python Engineer",
                        company="Acme",
                        location=request.location,
                        search_profile=request.profile_name,
                    ),
                    NormalizedJob(
                        source="indeed",
                        source_job_id="python-only",
                        title="Python API Engineer",
                        search_profile=request.profile_name,
                    ),
                ]
            return [
                NormalizedJob(
                    source=" indeed ",
                    source_job_id="shared-1",
                    title="Duplicate Python Engineer",
                    search_profile=request.profile_name,
                )
            ]

    class SequentialJobTrail(FakeJobTrail):
        def import_job(self, payload):
            self.imported.append(payload)
            job_id = f"j{len(self.imported)}"
            self.jobs[job_id] = {**payload, "id": job_id, "notes": []}
            return {"id": job_id}

    gateway = SequentialJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    adapter = ProfileDedupeAdapter()

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        source_adapters=(adapter,),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
            search_profiles=(
                automation.SearchProfile(name="python", locations=("remote",)),
                automation.SearchProfile(name="backend", locations=("remote",)),
            ),
        )
    )

    assert [request.profile_name for request in adapter.requests] == ["python", "backend"]
    assert gateway.imported == [
        {
            "source": "Indeed",
            "sourceJobId": " SHARED-1 ",
            "company": "Acme",
            "position": "Python Engineer",
            "location": "remote",
            "searchProfile": "python",
        },
        {
            "source": "indeed",
            "sourceJobId": "python-only",
            "position": "Python API Engineer",
            "searchProfile": "python",
        },
    ]
    assert result.searched == 3
    assert result.imported == 2
    assert result.scored == 2
    assert result.failures == ()
    assert result.profile_counts == {
        "python": {"searched": 2, "imported": 2, "duplicates": 0, "failures": 0},
        "backend": {"searched": 1, "imported": 0, "duplicates": 1, "failures": 0},
    }
    assert result.selected["searchProfiles"] == ["python", "backend"]


def test_automation_deduplicates_profiles_before_seen_cache_skip(tmp_path):
    class SharedJobAdapter:
        name = "profiles"

        def search(self, request):
            return [
                NormalizedJob(
                    source=" Indeed ",
                    source_job_id=" SHARED-1 ",
                    title="Shared Engineer",
                    search_profile=request.profile_name,
                )
            ]

    cache = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_000.0))
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        seen_cache=cache,
        source_adapters=(SharedJobAdapter(),),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
            search_profiles=(
                automation.SearchProfile(name="python", locations=("remote",)),
                automation.SearchProfile(name="backend", locations=("remote",)),
            ),
        )
    )

    assert len(gateway.imported) == 1
    assert result.imported == 1
    assert result.profile_counts == {
        "python": {"searched": 1, "imported": 1, "duplicates": 0, "failures": 0},
        "backend": {"searched": 1, "imported": 0, "duplicates": 1, "failures": 0},
    }
    assert result.selected["searchProfiles"] == ["python", "backend"]
    assert cache.should_skip("indeed", "shared-1", hours_old=72) is True


def test_automation_stale_cache_hit_does_not_invent_profile_provenance(tmp_path):
    class CachedJobAdapter:
        name = "profiles"

        def search(self, request):
            return [
                NormalizedJob(
                    source=" Indeed ",
                    source_job_id=" CACHED-1 ",
                    title="Already Seen Engineer",
                    search_profile=request.profile_name,
                )
            ]

    cache = SeenCache(tmp_path / "seen.json", clock=_CacheClock(1_000.0))
    cache.mark_seen("indeed", "cached-1")
    gateway = FakeJobTrail()

    result = JobSearchAutomation(
        gateway,
        seen_cache=cache,
        source_adapters=(CachedJobAdapter(),),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
            search_profiles=(
                automation.SearchProfile(name="python", locations=("remote",)),
            ),
        )
    )

    assert gateway.imported == []
    assert result.imported == 0
    assert result.scored == 0
    assert result.selected is None
    assert result.profile_counts == {
        "python": {"searched": 1, "imported": 0, "duplicates": 0, "failures": 0}
    }


def test_automation_profile_failure_is_counted_and_other_profiles_continue():
    class FailingProfileAdapter:
        name = "profiles"

        def search(self, request):
            if request.profile_name == "broken":
                raise RuntimeError("profile unavailable")
            return [
                NormalizedJob(
                    source="indeed",
                    source_job_id="ok-1",
                    title="Recovered Engineer",
                    search_profile=request.profile_name,
                )
            ]

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        source_adapters=(FailingProfileAdapter(),),
    ).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            locations=("remote",),
            search_profiles=(
                automation.SearchProfile(name="broken", locations=("remote",)),
                automation.SearchProfile(name="healthy", locations=("remote",)),
            ),
        )
    )

    assert gateway.imported == [
        {
            "source": "indeed",
            "sourceJobId": "ok-1",
            "position": "Recovered Engineer",
            "searchProfile": "healthy",
        }
    ]
    assert result.searched == 1
    assert result.imported == 1
    assert result.scored == 1
    assert result.failures == ("search:terminal:RuntimeError",)
    assert result.profile_counts == {
        "broken": {"searched": 0, "imported": 0, "duplicates": 0, "failures": 1},
        "healthy": {"searched": 1, "imported": 1, "duplicates": 0, "failures": 0},
    }


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


def test_selected_notification_includes_public_search_profiles():
    class SameJobProfileAdapter:
        def search(self, request):
            return [
                NormalizedJob(
                    source="indeed",
                    source_job_id="source-1",
                    title="Python Engineer",
                    company="Acme",
                    description="good",
                    source_url="https://jobs.test/1",
                    location="Remote",
                )
            ]

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()

    result = JobSearchAutomation(
        gateway,
        scorer=scorer,
        notifier=notifier,
        source_adapters=(SameJobProfileAdapter(),),
    ).run(
        config=AutomationConfig(
            search_profiles=(
                automation.SearchProfile(name="python"),
                automation.SearchProfile(name="backend"),
            ),
            base_url="http://jobtrail.example.com",
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    assert result.selected["searchProfiles"] == ["python", "backend"]
    message = json.loads(notifier.messages[0])
    assert message["searchProfiles"] == ["python", "backend"]


def test_selected_notification_contains_job_and_score_data(monkeypatch):
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()
    original_summary = automation.build_notification_summary
    summary_args = []

    def capture_summary(job, score, **_kwargs):
        summary_args.append((job, score))
        return original_summary(job, score, **_kwargs)

    monkeypatch.setattr(automation, "build_notification_summary", capture_summary)
    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            base_url="http://jobtrail.example.com",
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    assert summary_args[0][0] is gateway.jobs["j1"]
    assert summary_args[0][1]["score"] == 91
    message = json.loads(notifier.messages[0])
    assert set(message) == {
        "title",
        "company",
        "location",
        "score",
        "recommendation",
        "recommendationLabel",
        "strengths",
        "gaps",
        "jobUrl",
        "jobTrailLink",
        "runId",
    }
    assert message["title"] == "Python Engineer"
    assert message["company"] == "Acme"
    assert message["location"] == "Queretaro"
    assert message["jobUrl"] == "https://jobs.test/1"
    assert message["score"] == 91
    assert message["recommendation"] == "PRIORITY_APPLY"
    assert message["recommendationLabel"] == "Priority Apply"
    assert message["strengths"] == ["Python"]
    assert message["gaps"] == ["None"]
    assert message["jobTrailLink"] == "http://jobtrail.example.com/jobs/j1"
    import re as _re

    assert _re.match(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$", message["runId"])


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
                "--base-url",
                "http://127.0.0.1:8000",
            ],
            True,
        )
    ]
    assert result.scored == 1


def test_default_scorer_propagates_discovered_base_url(monkeypatch):
    """The scorer subprocess must target the URL this run actually used.

    Regression: the discovered/resolved base_url was applied to the search
    and import HTTP calls but never reached the scorer subprocess, which
    would otherwise silently read the (possibly stale) jobtrail_base_url
    committed in its own YAML config.
    """

    gateway = FakeJobTrail()
    calls = []

    def run(args, *, check):
        calls.append(args)
        gateway.jobs["j1"]["notes"] = [{"body": '[AI_JOB_SCORE_V1]\\n{"score":81}'}]

    monkeypatch.setattr("jobtrail_ai_scorer.automation.subprocess.run", run)
    JobSearchAutomation(gateway).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            scorer_command="jobtrail-ai-scorer",
            base_url="http://discovered-host:8000",
        )
    )

    assert calls == [
        [
            "jobtrail-ai-scorer",
            "score",
            "--config",
            "safe/config.yaml",
            "--job-id",
            "j1",
            "--force",
            "--base-url",
            "http://discovered-host:8000",
        ]
    ]


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
            "id": "j1",
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
        base_url="https://jobtrail.example.com",
    )

    assert set(summary) == {
        "title",
        "company",
        "location",
        "score",
        "recommendation",
        "recommendationLabel",
        "strengths",
        "gaps",
        "jobUrl",
        "jobTrailLink",
        "runId",
    }
    assert "FORBIDDEN" not in json.dumps(summary)
    assert "description" not in json.dumps(summary).lower()
    assert "candidate_profile" not in json.dumps(summary).lower()
    assert "prompt" not in json.dumps(summary).lower()
    assert len(summary["strengths"][0]) <= 200
    assert len(summary["gaps"][0]) <= 200
    assert summary["jobTrailLink"] == "https://jobtrail.example.com/jobs/j1"


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


def test_profile_hours_old_override_controls_seen_cache_ttl(tmp_path):
    clock = _CacheClock(1_000.0)
    cache = SeenCache(tmp_path / "seen.json", clock=clock)
    cache.mark_seen("indeed", "source-1")
    clock.t += 49 * 3600
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    result = JobSearchAutomation(gateway, scorer=scorer, seen_cache=cache).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            hours_old=72,
            search_profiles=(
                automation.SearchProfile(
                    name="recent",
                    locations=("Queretaro",),
                    hours_old=24,
                ),
            ),
        )
    )

    assert result.searched == 1
    assert result.imported == 1
    assert gateway.imported != []


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


# --- Retry/backoff wiring for idempotent calls ----------------------------


class _FlakySearchHTTPClient:
    """HTTP client stub that fails search N times before succeeding."""

    def __init__(self, *, search_failures: int, status_code: int = 503) -> None:
        self._search_failures = search_failures
        self._status_code = status_code
        self.posts: list[tuple[str, dict]] = []

    def post(self, path, *, json):
        self.posts.append((path, json))
        if path.endswith("/search") and self._search_failures > 0:
            self._search_failures -= 1
            request = httpx.Request("POST", "http://test/search")
            response = httpx.Response(self._status_code, request=request)
            raise httpx.HTTPStatusError(
                "transient", request=request, response=response
            )
        if path.endswith("/search"):
            return FakeResponse([])
        return FakeResponse({"id": "j1"})


def test_automation_search_retries_on_5xx_and_recovers(caplog):
    """Transient 5xx errors during search are retried and recovered silently."""

    client = JobTrailHTTPClient(
        "http://test",
        client=_FlakySearchHTTPClient(search_failures=2, status_code=503),
    )

    with caplog.at_level("WARNING", logger="jobtrail_ai_scorer.retry"):
        result = JobSearchAutomation(client).run(
            config=AutomationConfig(scorer_config_path="safe/config.yaml")
        )

    assert result.failures == ()
    # Two retry log records were emitted with the structured prefix.
    assert any("retry:" in record.getMessage() for record in caplog.records)


def test_automation_search_exhausts_retries_and_records_failure_metadata():
    """When search retries are exhausted, the failure carries retry metadata."""

    client = JobTrailHTTPClient(
        "http://test",
        client=_FlakySearchHTTPClient(search_failures=10, status_code=503),
    )

    result = JobSearchAutomation(client).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml")
    )

    assert result.searched == 0
    assert result.imported == 0
    assert result.scored == 0
    assert result.failures
    failure = result.failures[0]
    assert failure.startswith("search:exhausted")
    assert "HTTPStatusError" in failure


def test_automation_import_terminal_failure_records_metadata(monkeypatch):
    """When an import raises a terminal 4xx the failure string marks it terminal."""

    gateway = FakeJobTrail()

    def boom_import(payload):
        request = httpx.Request("POST", "http://test/import")
        response = httpx.Response(422, request=request)
        raise httpx.HTTPStatusError(
            "unprocessable", request=request, response=response
        )

    monkeypatch.setattr(gateway, "import_job", boom_import)
    result = JobSearchAutomation(gateway).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml")
    )

    # ``FakeJobTrail.search`` returns one job per location; the default
    # ``AutomationConfig`` declares two locations (Queretaro, remote).
    assert result.searched == 2
    assert result.imported == 0
    # One terminal import failure per location that surfaced the job.
    assert sum(
        1
        for failure in result.failures
        if failure.startswith("import:terminal") and "HTTPStatusError" in failure
    ) == 2


def test_automation_scorer_retries_transient_failure(monkeypatch):
    """Scorer subprocess failures are retried before giving up."""

    gateway = FakeJobTrail()
    attempts = {"count": 0}

    def flaky_scorer(job_id, _config_path):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise httpx.ConnectError("transient")
        gateway.jobs[job_id]["notes"] = [
            {"body": '[AI_JOB_SCORE_V1]\\n{"score":82}'}
        ]

    notifier = FakeNotifier()
    result = JobSearchAutomation(gateway, scorer=flaky_scorer, notifier=notifier).run(
        config=AutomationConfig(scorer_config_path="safe/config.yaml")
    )

    assert attempts["count"] == 2
    assert result.scored == 1
    assert result.failures == ()


# --- WHATSAPP_NOTIFY_ON_FAILURE opt-in -----------------------------------


def test_whatsapp_notify_on_failure_default_is_off():
    """Without WHATSAPP_NOTIFY_ON_FAILURE, failures never trigger a notification."""

    config = AutomationConfig.from_env({})
    assert config.notify_on_failure is False


def test_whatsapp_notify_on_failure_env_is_parsed():
    """The env var is parsed into the boolean ``notify_on_failure`` field."""

    config = AutomationConfig.from_env({"WHATSAPP_NOTIFY_ON_FAILURE": "1"})
    assert config.notify_on_failure is True
    config = AutomationConfig.from_env({"WHATSAPP_NOTIFY_ON_FAILURE": "true"})
    assert config.notify_on_failure is True
    config = AutomationConfig.from_env({"WHATSAPP_NOTIFY_ON_FAILURE": "0"})
    assert config.notify_on_failure is False


def test_whatsapp_notify_on_failure_sends_bounded_summary_when_set(monkeypatch):
    """When the opt-in is on, the notifier receives a bounded failure summary."""

    gateway = FakeJobTrail()

    def boom_import(payload):
        request = httpx.Request("POST", "http://test/import")
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError(
            "transient", request=request, response=response
        )

    monkeypatch.setattr(gateway, "import_job", boom_import)
    notifier = FakeNotifier()

    JobSearchAutomation(gateway, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            notify_on_failure=True,
            whatsapp_command="./local-notify.sh",
        )
    )

    assert len(notifier.messages) == 1
    payload = json.loads(notifier.messages[0])
    assert payload["kind"] == "failure_summary"
    assert payload["failure_count"] >= 1
    # The summary must not leak the description or any sensitive fields.
    assert "description" not in json.dumps(payload).lower()


def test_whatsapp_notify_on_failure_sends_summary_even_without_match(monkeypatch):
    """Failures alone (no selected match) still produce a notification."""

    gateway = FakeJobTrail()

    def boom_import(payload):
        request = httpx.Request("POST", "http://test/import")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError(
            "transient", request=request, response=response
        )

    monkeypatch.setattr(gateway, "import_job", boom_import)
    notifier = FakeNotifier()

    JobSearchAutomation(gateway, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            notify_enabled=False,
            notify_on_failure=True,
            whatsapp_command="./local-notify.sh",
        )
    )

    # Only the failure summary is sent because notify_enabled is off.
    assert len(notifier.messages) == 1
    payload = json.loads(notifier.messages[0])
    assert payload["kind"] == "failure_summary"


# --- Triangulation: additional retry wiring cases ----------------------------


@pytest.mark.parametrize(
    "error",
    [
        httpx.HTTPStatusError(
            "transient",
            request=httpx.Request("POST", "http://test/import"),
            response=httpx.Response(
                503, request=httpx.Request("POST", "http://test/import")
            ),
        ),
        TimeoutError("timed out"),
        ConnectionError("connection lost"),
    ],
    ids=["5xx", "timeout", "connection"],
)
def test_automation_import_failure_is_not_retried_and_is_partial(error):
    """A lost import response must not cause a duplicate POST."""

    class _FailedImportHTTPClient:
        def __init__(self) -> None:
            self.posts: list[tuple[str, dict]] = []

        def post(self, path, *, json):
            self.posts.append((path, json))
            if path.endswith("/import"):
                raise error
            return FakeResponse(
                [{"id": "x", "site": "indeed", "title": "T", "company": "C"}]
            )

    recorder = _FailedImportHTTPClient()
    client = JobTrailHTTPClient("http://test", client=recorder)
    result = JobSearchAutomation(client).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml", locations=("Queretaro",)
        )
    )

    assert [path for path, _ in recorder.posts].count("/api/discover/import") == 1
    assert result.searched == 1
    assert result.imported == 0
    assert result.failures and result.failures[0].startswith("import:")
    assert type(error).__name__ in result.failures[0]


def test_automation_get_retries_on_transient_failure():
    class _FlakyGetHTTPClient:
        attempts = 0

        def get(self, path):
            self.attempts += 1
            if self.attempts < 3:
                raise httpx.ReadTimeout("transient")
            return FakeResponse({"id": "j1", "notes": []})

    recorder = _FlakyGetHTTPClient()
    client = JobTrailHTTPClient("http://test", client=recorder)
    assert client.get_job("j1") == {"id": "j1", "notes": []}
    assert recorder.attempts == 3


def test_automation_no_notification_when_nothing_to_report():
    """No notifier call when neither a best match nor a failure summary applies."""

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs

    notifier = FakeNotifier()
    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    # Existing behavior: a best match above the threshold triggers one notification.
    assert len(notifier.messages) == 1

    # With both flags off and no failures, the notifier must not be called again.
    notifier_off = FakeNotifier()
    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier_off).run(
        config=AutomationConfig(
            scorer_config_path="safe/config.yaml",
            notify_enabled=False,
            notify_on_failure=False,
        )
    )
    assert notifier_off.messages == []


_RUN_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-[a-f0-9]{6}$")


def test_automation_failure_summary_is_bounded_and_safe():
    """The failure summary exposes only abstract labels, no sensitive content."""

    summary = automation.build_failure_summary(
        (
            "import:exhausted:HTTPStatusError",
            "search:exhausted:ConnectError",
            "score:j1:terminal:ValueError",
            "read:j2:exhausted:TimeoutError",
            "import:exhausted:HTTPStatusError",
            "import:exhausted:HTTPStatusError",
        ),
        max_items=5,
    )

    assert summary["kind"] == "failure_summary"
    assert summary["failure_count"] == 6
    assert len(summary["failures"]) == 5
    # All labels are abstract; no raw messages or sensitive fields.
    for label in summary["failures"]:
        assert isinstance(label, str)
        assert "sensitive" not in label
        assert "description" not in label.lower()


# --- NotificationBuilder end-to-end wiring -------------------------------



def test_compose_notification_includes_three_new_fields_in_run():
    """``JobTrailAutomation.run`` exposes jobTrailLink, recommendationLabel, and runId."""

    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()

    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            base_url="http://jobtrail.example.com",
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    assert len(notifier.messages) == 1
    message = json.loads(notifier.messages[0])
    # The three new fields are present and stable.
    assert message["jobTrailLink"].endswith("/jobs/j1")
    assert message["jobTrailLink"].startswith("http://jobtrail.example.com")
    assert message["recommendationLabel"] in {"Apply", "Priority Apply", "Review", "Skip"}
    assert _RUN_ID_PATTERN.match(message["runId"])


def test_compose_notification_link_uses_whatsapp_short_url_base(monkeypatch):
    """The run path honors WHATSAPP_SHORT_URL_BASE via NotificationBuilder.from_env."""

    monkeypatch.setenv("WHATSAPP_SHORT_URL_BASE", "https://sho.rt")
    gateway = FakeJobTrail()
    scorer = FakeScorer()
    scorer.jobs = gateway.jobs
    notifier = FakeNotifier()

    JobSearchAutomation(gateway, scorer=scorer, notifier=notifier).run(
        config=AutomationConfig(
            base_url="http://jobtrail.example.com",
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    message = json.loads(notifier.messages[0])
    assert message["jobTrailLink"].startswith("https://sho.rt/")
    assert message["jobTrailLink"].endswith("/jobs/j1")
    assert "jobtrail.example.com" not in message["jobTrailLink"]


def test_compose_notification_redacts_sensitive_substrings_in_run():
    """CV/profile/prompt/credential substrings never reach the notifier."""

    gateway = FakeJobTrail()

    def score(job_id, config_path):
        # Inject sentinels in the strengths/gaps so the scrub step is exercised
        # through the full run pipeline.
        gateway.jobs[job_id]["notes"] = [
            {
                "body": (
                    "[AI_JOB_SCORE_V1]\n"
                    + json.dumps(
                        {
                            "score": 91,
                            "recommendation": "PRIORITY_APPLY",
                            "strengths": ["PROMPT_SENTINEL contained"],
                            "gaps": ["RESUME_SENTINEL contained"],
                            "reasoning": "PROFILE_SENTINEL reasoning",
                        }
                    )
                )
            }
        ]

    notifier = FakeNotifier()
    JobSearchAutomation(gateway, scorer=score, notifier=notifier).run(
        config=AutomationConfig(
            base_url="http://jobtrail.example.com",
            scorer_config_path="safe/config.yaml",
            notify_enabled=True,
        )
    )

    rendered = notifier.messages[0]
    for sentinel in (
        "RESUME_SENTINEL",
        "PROFILE_SENTINEL",
        "PROMPT_SENTINEL",
        "CREDENTIAL_SENTINEL",
    ):
        assert sentinel not in rendered


# --- Lever adapter integration --------------------------------------------


def test_automation_failing_lever_adapter_does_not_block_jobspy_import():
    """A ``LeverSourceAdapter`` that raises during ``search`` must be caught
    at the orchestrator as a ``search:`` failure while the JobSpy adapter
    keeps importing its results.

    The test wires a real :class:`LeverSourceAdapter` through an
    :class:`httpx.MockTransport` so the integration exercises the actual
    search() raise site rather than a hand-rolled fake.
    """

    from jobtrail_ai_scorer.retry import RetryPolicy
    from jobtrail_ai_scorer.sources.lever import LeverSourceAdapter

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    client = httpx.Client(transport=httpx.MockTransport(failing_handler))
    lever_adapter = LeverSourceAdapter(
        boards=("acme",),
        client=client,
        retry_policy=RetryPolicy(max_attempts=1, base_delay=0.01, max_delay=0.01),
    )
    try:
        gateway = FakeJobTrail()
        scorer = FakeScorer()
        scorer.jobs = gateway.jobs

        result = JobSearchAutomation(
            gateway,
            scorer=scorer,
            source_adapters=(JobSpySourceAdapter(gateway), lever_adapter),
        ).run(
            config=AutomationConfig(
                scorer_config_path="safe/config.yaml",
                locations=("Queretaro",),
                ats_boards=AtsBoardConfig(lever_boards=("acme",)),
            )
        )

        # JobSpy still imported one result despite the Lever failure.
        assert result.searched == 1
        assert result.imported == 1
        assert result.scored == 1
        assert result.selected is not None
        assert result.selected["title"] == "Python Engineer"
        # The Lever failure is recorded on the run summary with the exception
        # type only (no board URL or credentials leak).
        assert any(
            "LeverTransientError" in failure for failure in result.failures
        )
    finally:
        client.close()


def test_automation_healthy_lever_adapter_contributes_alongside_jobspy():
    """A healthy ``LeverSourceAdapter`` alongside JobSpy must contribute its
    results without interfering with the JobSpy pipeline."""

    from jobtrail_ai_scorer.sources.lever import (
        DEFAULT_BASE_URL,
        LeverSourceAdapter,
        normalize_lever_posting,
    )

    # Build a tiny payload that maps to the design contract (Lever fields are
    # normalized via ``normalize_lever_posting`` so the test exercises the
    # real normalization path too).
    posting = {
        "id": "lever-1",
        "text": "Senior Python Developer",
        "description": "<p>Build amazing things.</p>",
        "applyUrl": "https://jobs.lever.co/acme/lever-1",
        "categories": {"location": "Mexico City", "commitment": "Full-time"},
    }
    normalized = normalize_lever_posting(
        posting,
        company="acme",
        profile_name="default",
        retrieved_at=None,
    )

    def lever_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[posting], request=request)

    client = httpx.Client(transport=httpx.MockTransport(lever_handler))
    lever_adapter = LeverSourceAdapter(
        boards=("acme",), client=client, base_url=DEFAULT_BASE_URL
    )

    # ``FakeJobTrail.import_job`` returns ``j1`` for every import, so the
    # second adapter's import would be skipped by the orchestrator's
    # ``str(job_id) not in ids`` check. Use a subclass that hands out a
    # fresh id per import so both adapters can be imported independently.
    class _SequentialFakeJobTrail(FakeJobTrail):
        def __init__(self) -> None:
            super().__init__()
            self._counter = 0

        def import_job(self, payload):
            self._counter += 1
            self.imported.append(payload)
            job_id = f"j{self._counter}"
            self.jobs[job_id] = {**payload, "id": job_id, "notes": []}
            return {"id": job_id}

    try:
        gateway = _SequentialFakeJobTrail()
        scorer = FakeScorer()
        scorer.jobs = gateway.jobs

        result = JobSearchAutomation(
            gateway,
            scorer=scorer,
            source_adapters=(JobSpySourceAdapter(gateway), lever_adapter),
        ).run(
            config=AutomationConfig(
                scorer_config_path="safe/config.yaml",
                locations=("Queretaro",),
                ats_boards=AtsBoardConfig(lever_boards=("acme",)),
            )
        )

        # Both adapters contributed their results without any failure.
        assert result.failures == ()
        assert result.searched == 2
        assert result.imported == 2
        # The Lever job is normalized through the real helper so the
        # location includes both ``categories.location`` and
        # ``categories.commitment``.
        assert normalized.location == "Mexico City / Full-time"
        assert normalized.company == "acme"
        assert normalized.source_job_id == "lever-1"
    finally:
        client.close()
