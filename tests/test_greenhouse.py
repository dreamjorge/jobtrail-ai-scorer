from datetime import datetime, timezone

import httpx
import pytest

from jobtrail_ai_scorer.retry import RetryPolicy
from jobtrail_ai_scorer.sources import SourceSearchRequest
from jobtrail_ai_scorer.sources.greenhouse import (
    GreenhouseHttpError,
    GreenhouseSourceAdapter,
    GreenhouseTransientError,
)


def request(results_wanted=10, profile_name="backend"):
    return SourceSearchRequest(("indeed",), "ignored", "ignored", results_wanted, 24, False, profile_name)


def test_greenhouse_normalizes_and_caps_across_boards_and_uses_profile():
    def handler(req):
        board = req.url.path.split("/")[-2]
        return httpx.Response(200, json={"jobs": [
            {"id": board + "-1", "title": "Engineer", "content": "<p>Build <b>things</b></p>",
             "absolute_url": "https://jobs.example/1", "location": {"name": "Remote"}, "updated_at": "x"},
            {"id": board + "-2", "title": "Second", "content": "<p>More</p>", "location": {"name": "NYC"}},
        ]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = GreenhouseSourceAdapter(("acme", "other"), client=client, clock=lambda: datetime(2024, 1, 2, tzinfo=timezone.utc))
    jobs = adapter.search(request(3, "ml"))
    assert [job.source_job_id for job in jobs] == ["acme-1", "acme-2", "other-1"]
    assert jobs[0].description == "Build things"
    assert jobs[0].search_profile == "ml"
    assert jobs[0].company == "acme"
    assert jobs[0].remote is True
    assert jobs[0].retrieved_at == "2024-01-02T00:00:00Z"


def test_greenhouse_get_path_query_and_ignores_search_scope():
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(200, json={"jobs": []}))[1]))
    GreenhouseSourceAdapter(("my-board",), client=client).search(request())
    assert seen[0].method == "GET"
    assert str(seen[0].url) == "https://boards-api.greenhouse.io/v1/boards/my-board/jobs?content=true"


def test_greenhouse_4xx_is_terminal():
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    with pytest.raises(GreenhouseHttpError) as exc:
        GreenhouseSourceAdapter(("acme",), client=client).search(request())
    assert exc.value.status_code == 404


def test_greenhouse_retries_5xx_then_recovers_and_exhaustion_is_typed():
    calls = []
    def recovering(req):
        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"jobs": []})
    adapter = GreenhouseSourceAdapter(("acme",), client=httpx.Client(transport=httpx.MockTransport(recovering)),
                                      retry_policy=RetryPolicy(max_attempts=2, base_delay=0, max_delay=0), retry_sleep=lambda _: None)
    assert adapter.search(request()) == []
    assert len(calls) == 2
    failing = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    adapter = GreenhouseSourceAdapter(("acme",), client=failing, retry_policy=RetryPolicy(max_attempts=2, base_delay=0, max_delay=0), retry_sleep=lambda _: None)
    with pytest.raises(GreenhouseTransientError) as exc:
        adapter.search(request())
    assert exc.value.attempts == 2


def test_greenhouse_skips_malformed_rows_and_top_level():
    payloads = iter([{"jobs": [None, {"title": "missing"}, {"id": 7, "title": "ok"}]}, {"jobs": "bad"}])
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=next(payloads))))
    adapter = GreenhouseSourceAdapter(("acme", "other"), client=client)
    assert [job.source_job_id for job in adapter.search(request())] == ["7"]


def test_greenhouse_close_only_owned_client():
    supplied = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"jobs": []})))
    adapter = GreenhouseSourceAdapter(("acme",), client=supplied)
    adapter.close()
    assert not supplied.is_closed
    adapter = GreenhouseSourceAdapter(("acme",))
    client = adapter._client
    adapter.close()
    assert client.is_closed
