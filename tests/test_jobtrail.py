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


def test_get_job_encodes_job_id_as_one_path_segment():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://jobs.test/api/jobs/j%2F1%3Fsource%3Dtest"
        return httpx.Response(200, json={"id": "j/1?source=test"}, request=request)

    assert make_client(httpx.MockTransport(handler)).get_job("j/1?source=test") == {
        "id": "j/1?source=test"
    }


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


def test_malformed_success_response_raises_domain_specific_error():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"{not json", request=request)
    )

    with pytest.raises(JobTrailApiError):
        make_client(transport).list_jobs()


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        (lambda client: client.list_jobs(), {"id": "j1"}),
        (lambda client: client.get_job("j1"), [{"id": "j1"}]),
    ],
)
def test_response_with_unexpected_top_level_shape_raises_domain_error(operation, payload):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )

    with pytest.raises(JobTrailApiError):
        operation(make_client(transport))


def test_close_closes_only_an_internally_owned_http_client():
    owned_client = JobTrailClient("https://jobs.test")
    injected_http_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    injected_client = JobTrailClient("https://jobs.test", http_client=injected_http_client)

    owned_client.close()
    injected_client.close()

    assert owned_client._http_client.is_closed
    assert not injected_http_client.is_closed
    injected_http_client.close()


def test_context_manager_closes_internally_owned_http_client():
    with JobTrailClient("https://jobs.test") as client:
        http_client = client._http_client
        assert not http_client.is_closed

    assert http_client.is_closed
