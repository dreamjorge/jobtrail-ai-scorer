import json
import tomllib
from pathlib import Path

import httpx
import pytest

from jobtrail_ai_scorer.jobtrail import JobTrailApiError, JobTrailClient


def test_project_declares_httpx_runtime_dependency():
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    dependencies = tomllib.loads(project_file.read_text())["project"]["dependencies"]

    assert any(dependency.startswith("httpx") for dependency in dependencies)


def make_client(handler: httpx.MockTransport) -> JobTrailClient:
    return JobTrailClient(
        "https://jobs.test",
        http_client=httpx.Client(transport=handler),
    )


def test_list_jobs_gets_job_collection():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://jobs.test/api/jobs"
        return httpx.Response(200, json=[{"id": "j1"}], request=request)

    assert make_client(httpx.MockTransport(handler)).list_jobs() == [{"id": "j1"}]


def test_get_job_gets_job_by_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://jobs.test/api/jobs/j1"
        return httpx.Response(200, json={"id": "j1"}, request=request)

    assert make_client(httpx.MockTransport(handler)).get_job("j1") == {"id": "j1"}


def test_add_note_posts_body_and_accepts_created_response():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, request=request)

    client = make_client(httpx.MockTransport(handler))

    client.add_note("j1", "[AI_JOB_SCORE_V1]\n...")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://jobs.test/api/jobs/j1/notes"
    assert json.loads(request.content) == {"body": "[AI_JOB_SCORE_V1]\n..."}


@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.list_jobs(),
        lambda client: client.get_job("j1"),
        lambda client: client.add_note("j1", "[AI_JOB_SCORE_V1]\n..."),
    ],
)
def test_http_status_failures_raise_domain_specific_error(operation):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, request=request)
    )

    with pytest.raises(JobTrailApiError):
        operation(make_client(transport))


def test_transport_failures_raise_domain_specific_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(JobTrailApiError):
        make_client(httpx.MockTransport(handler)).list_jobs()
