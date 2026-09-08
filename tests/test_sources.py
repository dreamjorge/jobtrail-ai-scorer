from jobtrail_ai_scorer.sources import (
    JobSpySourceAdapter,
    NormalizedJob,
    SourceSearchRequest,
)


def test_normalized_job_exports_import_payload_without_none_values():
    job = NormalizedJob(
        source="indeed",
        source_job_id="source-1",
        title="Python Engineer",
        company="Acme",
        description="Build APIs",
        source_url="https://jobs.test/1",
        location="Remote",
        remote=True,
        salary_min=None,
        salary_max=120000,
        salary_currency="USD",
        job_type=None,
        search_profile="backend",
        retrieved_at="2026-09-08T00:00:00Z",
        metadata={"site": "indeed"},
    )

    assert job.identity == ("indeed", "source-1")
    assert job.to_import_payload() == {
        "source": "indeed",
        "sourceJobId": "source-1",
        "company": "Acme",
        "position": "Python Engineer",
        "description": "Build APIs",
        "jobUrl": "https://jobs.test/1",
        "location": "Remote",
        "remote": True,
        "salaryMax": 120000,
        "salaryCurrency": "USD",
        "searchProfile": "backend",
        "retrievedAt": "2026-09-08T00:00:00Z",
        "metadata": {"site": "indeed"},
    }


def test_source_search_request_exports_legacy_jobspy_payload():
    request = SourceSearchRequest(
        sites=("linkedin", "indeed"),
        search_term="python",
        location="remote",
        results_wanted=5,
        hours_old=24,
        is_remote=True,
        profile_name="backend",
    )

    assert request.to_jobspy_payload() == {
        "sites": ["linkedin", "indeed"],
        "searchTerm": "python",
        "location": "remote",
        "resultsWanted": 5,
        "hoursOld": 24,
        "isRemote": True,
    }


class RecordingSearchGateway:
    def __init__(self, jobs):
        self.jobs = jobs
        self.payloads = []

    def search(self, payload):
        self.payloads.append(payload)
        return self.jobs


def test_jobspy_adapter_maps_raw_jobs_to_normalized_jobs():
    gateway = RecordingSearchGateway(
        [
            {
                "site": "indeed",
                "id": "source-1",
                "company": "Acme",
                "title": "Python Engineer",
                "description": "Build APIs",
                "job_url": "https://jobs.test/1",
                "location": "Remote",
                "is_remote": True,
                "min_amount": 100000,
                "max_amount": 120000,
                "currency": "USD",
                "job_type": "fulltime",
            }
        ]
    )
    adapter = JobSpySourceAdapter(gateway)
    request = SourceSearchRequest(
        sites=("indeed",),
        search_term="python",
        location="remote",
        results_wanted=1,
        hours_old=24,
        is_remote=True,
        profile_name="backend",
    )

    jobs = adapter.search(request)

    assert gateway.payloads == [request.to_jobspy_payload()]
    assert jobs[0].search_profile == "backend"
    assert jobs == [
        NormalizedJob(
            source="indeed",
            source_job_id="source-1",
            title="Python Engineer",
            company="Acme",
            description="Build APIs",
            source_url="https://jobs.test/1",
            location="Remote",
            remote=True,
            salary_min=100000,
            salary_max=120000,
            salary_currency="USD",
            job_type="fulltime",
            search_profile="backend",
        )
    ]
