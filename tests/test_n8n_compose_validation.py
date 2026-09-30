import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "validation" / "n8n" / "docker-compose.yml"
README = ROOT / "validation" / "n8n" / "README.md"
WORKFLOW = ROOT / "validation" / "n8n" / "workflows" / "job-feedback.json"
FEEDBACK_FIXTURE = ROOT / "tests" / "fixtures" / "n8n" / "job-feedback-event.json"

FORBIDDEN_TEXT = (
    "/DATA",
    "/AppData",
    "candidate-profile",
    "credentials",
    "PRIVATE",
    "env_file",
    "jobtrail",
    "hermes",
    "whatsapp",
)


def _compose_text():
    return FIXTURE.read_text()


def _compose():
    return yaml.safe_load(_compose_text())


def _service(compose):
    services = compose.get("services", {})
    assert set(services) == {"n8n-validation"}, "fixture must contain only n8n-validation"
    return services["n8n-validation"]


def _assert_profile_gated(compose):
    service = _service(compose)
    assert service.get("profiles") == ["n8n-validation"], "fixture must be profile gated"


def _assert_required_image(text, service):
    assert '"${N8N_IMAGE:?N8N_IMAGE must name a reviewed image}"' in text
    assert "latest" not in text.lower(), "mutable latest image is forbidden"
    assert "image" in service and len(service) >= 1
    assert not any(key in service for key in ("build", "image_file")), "alternate image source is forbidden"


def _parse_host_ip(port):
    if isinstance(port, str):
        return port.split(":", 1)[0] if port.count(":") >= 2 else None
    if isinstance(port, dict):
        return port.get("host_ip")
    return None


def _assert_loopback_only(compose):
    service = _service(compose)
    ports = service.get("ports", [])
    assert ports, "validation endpoint must declare a host binding"
    for port in ports:
        assert _parse_host_ip(port) == "127.0.0.1", f"unsafe non-loopback host binding: {port!r}"


def _assert_named_persistence(compose):
    service = _service(compose)
    volumes = service.get("volumes", [])
    assert len(volumes) == 1
    mount = volumes[0]
    assert isinstance(mount, str)
    source, target, *_ = mount.split(":")
    assert source == "n8n-validation-data"
    assert target == "/home/node/.n8n"
    assert not source.startswith(("/", "~", "${"))
    assert compose.get("volumes") == {"n8n-validation-data": {"driver": "local"}}


def _assert_no_production_dependencies(compose):
    assert "networks" not in compose
    service = _service(compose)
    forbidden = {"network_mode", "depends_on", "links", "env_file", "secrets", "configs"}
    assert not forbidden.intersection(service), "production dependency is forbidden"
    assert "networks" not in service


def _assert_no_private_dependencies(compose, text):
    service = _service(compose)
    assert "env_file" not in service
    environment = service.get("environment", {})
    keys = environment.keys() if isinstance(environment, dict) else environment
    assert not any(any(word in key.upper() for word in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL")) for key in keys)
    assert not any(fragment.lower() in text.lower() for fragment in FORBIDDEN_TEXT)
    for mount in service.get("volumes", []):
        assert isinstance(mount, str) and not mount.startswith(("/", "~", "${"))


def test_fixture_is_profile_gated_and_has_one_validation_service():
    compose = _compose()
    _assert_profile_gated(compose)
    unsafe = deepcopy(compose)
    unsafe["services"]["n8n-validation"]["profiles"] = []
    with pytest.raises(AssertionError, match="profile"):
        _assert_profile_gated(unsafe)


def test_image_requires_explicit_n8n_image_without_mutable_default():
    text = _compose_text()
    service = _service(_compose())
    _assert_required_image(text, service)
    with pytest.raises(AssertionError, match="latest"):
        _assert_required_image(text.replace("${N8N_IMAGE:?N8N_IMAGE must name a reviewed image}", "latest"), service)


def test_every_host_binding_is_loopback_only():
    compose = _compose()
    _assert_loopback_only(compose)
    unsafe = deepcopy(compose)
    unsafe["services"]["n8n-validation"]["ports"][0] = "0.0.0.0:${N8N_HOST_PORT:-5678}:5678"
    with pytest.raises(AssertionError, match="non-loopback"):
        _assert_loopback_only(unsafe)


def test_persistence_is_named_and_validation_scoped():
    compose = _compose()
    _assert_named_persistence(compose)
    unsafe = deepcopy(compose)
    unsafe["services"]["n8n-validation"]["volumes"][0] = "/private/data:/home/node/.n8n"
    with pytest.raises(AssertionError):
        _assert_named_persistence(unsafe)


def test_fixture_has_no_production_network_or_service_dependencies():
    compose = _compose()
    _assert_no_production_dependencies(compose)
    unsafe = deepcopy(compose)
    unsafe["networks"] = {"production": {}}
    with pytest.raises(AssertionError, match="production"):
        _assert_no_production_dependencies(unsafe)


def test_fixture_has_no_credentials_env_files_or_private_mounts():
    compose = _compose()
    _assert_no_private_dependencies(compose, _compose_text())
    unsafe = deepcopy(compose)
    unsafe["services"]["n8n-validation"]["environment"] = {"N8N_API_TOKEN": "secret"}
    with pytest.raises(AssertionError):
        _assert_no_private_dependencies(unsafe, _compose_text())


def test_documentation_marks_smoke_optional_local_and_non_ci():
    text = README.read_text().lower()
    for phrase in ("operator", "localhost", "loopback", "optional", "non-ci", "cleanup", "hermes", "whatsapp", "provider", "production"):
        assert phrase in text, f"README must state {phrase} boundary"
    assert "n8n_image=<reviewed-image> docker compose --profile n8n-validation -f validation/n8n/docker-compose.yml up -d" in text
    assert "python -m pytest tests/test_n8n_compose_validation.py -v" in text
    assert "not scoring" in text and "not" in text and "completion" in text


def test_fixture_is_not_referenced_by_production_startup():
    production_files = [ROOT / "docker-compose.yml", ROOT / "docs" / "runtime-automation.md"]
    for path in production_files:
        text = path.read_text().lower()
        assert "validation/n8n" not in text
        assert "n8n-validation" not in text


def _workflow():
    return json.loads(WORKFLOW.read_text())


def _workflow_nodes(workflow):
    return {node["name"]: node for node in workflow["nodes"]}


def _validation_code(workflow):
    return _workflow_nodes(workflow)["Validate bounded feedback and replay"]["parameters"]["jsCode"]


def test_job_feedback_workflow_has_exact_local_webhook_contract():
    workflow = _workflow()
    nodes = _workflow_nodes(workflow)
    assert set(nodes) == {
        "Local operator webhook",
        "Validate bounded feedback and replay",
        "Sanitized feedback response",
    }

    webhook = nodes["Local operator webhook"]
    assert webhook["type"] == "n8n-nodes-base.webhook"
    assert webhook["parameters"]["httpMethod"] == "POST"
    assert webhook["parameters"]["path"] == "job-feedback"
    assert webhook["parameters"]["responseMode"] == "responseNode"
    assert webhook["webhookId"] == "job-feedback-local-only"

    response = nodes["Sanitized feedback response"]
    assert response["type"] == "n8n-nodes-base.respondToWebhook"
    assert response["parameters"]["respondWith"] == "json"
    assert response["parameters"]["responseBody"] == "={{ $json }}"


def test_job_feedback_workflow_has_exact_webhook_validate_response_topology():
    workflow = _workflow()
    assert workflow["connections"] == {
        "Local operator webhook": {
            "main": [[{
                "node": "Validate bounded feedback and replay",
                "type": "main",
                "index": 0,
            }]],
        },
        "Validate bounded feedback and replay": {
            "main": [[{
                "node": "Sanitized feedback response",
                "type": "main",
                "index": 0,
            }]],
        },
    }


def test_job_feedback_workflow_is_inactive_profile_gated_and_local_only():
    workflow = _workflow()
    assert workflow["active"] is False
    assert workflow["settings"]["executionOrder"] == "v1"
    assert workflow["meta"]["templateCredsSetupCompleted"] is False
    assert all(
        "cron" not in node["type"].lower()
        and "schedule" not in node["type"].lower()
        and "http://" not in json.dumps(node).lower()
        and "https://" not in json.dumps(node).lower()
        for node in workflow["nodes"]
    )
    compose = _compose()
    assert _service(compose)["profiles"] == ["n8n-validation"]
    assert _parse_host_ip(_service(compose)["ports"][0]) == "127.0.0.1"


def test_job_feedback_validation_has_exact_actions_identity_expiry_token_replay_and_sanitization():
    code = _validation_code(_workflow())
    assert "const allowedFields = new Set(['schema_version', 'event', 'run', 'source', 'sourceJob', 'action', 'token', 'expiry']);" in code
    assert "if (!['applied', 'dismissed', 'interesting'].includes(input.action)) fail('action');" in code
    for marker in (
        "input.schema_version !== 1",
        "input.event !== 'job_feedback'",
        "const stringFields = ['event', 'run', 'source', 'sourceJob', 'action', 'token'];",
        "Number.isSafeInteger(input.expiry)",
        "input.expiry <= Math.floor(Date.now() / 1000)",
        "input.token.endsWith(`.${input.expiry}`)",
        "$getWorkflowStaticData('global')",
        "state.replayedTokens",
        "const replayKey = input.token",
        "if (state.replayedTokens[replayKey]) fail('replay')",
        "for (const field of stringFields)",
        "input[field].length > 128",
        "MAX_REPLAY_ENTRIES = 1024",
        "expiresAt: input.expiry * 1000",
        "entry.expiresAt <= now",
        "Object.keys(state.replayedTokens).length >= MAX_REPLAY_ENTRIES",
        "sort((left, right)",
        "left.localeCompare(right)",
        "Object.keys(input)",
        "unknown field",
        "duplicate action",
        "accepted: true",
        "sourceJob: input.sourceJob",
    ):
        assert marker in code, f"validation code must contain {marker}"
    assert "REPLAY_TTL_MS" not in code
    assert "returnData" not in code
    assert "JSON.stringify([input.token, input.action, input.run, input.source, input.sourceJob])" not in code


def test_workflow_code_executes_with_n8n_helpers():
    import subprocess

    code = _validation_code(_workflow())
    event = json.loads(FEEDBACK_FIXTURE.read_text())
    harness = r"""
const code = JSON.parse(process.argv[1]);
const event = JSON.parse(process.argv[2]);
const state = {};
const execute = new Function('$input', '$getWorkflowStaticData', code);
const invoke = (input) => execute({first: () => ({json: {body: input}})}, (scope) => {
  if (scope !== 'global') throw Error('wrong scope');
  return state;
});
const accepted = invoke(event)[0].json;
if (!accepted.accepted || accepted.token || accepted.expiry) throw Error('unsafe response');
const rejects = (input, reason) => {
  try { invoke(input); } catch (error) {
    if (error.message.includes(reason)) return;
    throw error;
  }
  throw Error('unexpected acceptance: ' + reason);
};
rejects({...event, action: 'applied'}, 'replay');
rejects({...event, action: 'unknown'}, 'action');
rejects({...event, extra: 'synthetic'}, 'unknown field');
rejects({...event, expiry: 1000000000, token: 'synthetic-token.1000000000'}, 'expired');
rejects({...event, token: 'synthetic-token.4102444799'}, 'token');
state.replayedTokens = {expired: {seenAt: 0, expiresAt: 0}};
for (let i = 0; i < 1024; i++) state.replayedTokens['entry-' + i] = {seenAt: i, expiresAt: 4102444800000};
invoke({...event, token: 'fresh-synthetic.4102444800'});
if (Object.keys(state.replayedTokens).length !== 1024 || state.replayedTokens.expired || state.replayedTokens['entry-0']) throw Error('replay bound');
console.log('workflow helper execution passed');
"""
    result = subprocess.run(["node", "-e", harness, json.dumps(code), json.dumps(event)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "workflow helper execution passed" in result.stdout


def test_job_feedback_fixture_is_synthetic_and_schema_bounded():
    event = json.loads(FEEDBACK_FIXTURE.read_text())
    assert event == {
        "schema_version": 1,
        "event": "job_feedback",
        "run": "synthetic-run-001",
        "source": "operator_fixture",
        "sourceJob": "synthetic-job-001",
        "action": "interesting",
        "token": "synthetic-token-001.4102444800",
        "expiry": 4102444800,
    }
    text = FEEDBACK_FIXTURE.read_text().lower()
    for marker in ("private", "cv", "profile", "prompt", "credential", "password", "secret"):
        assert marker not in text


def test_readme_documents_feedback_fixture_boundaries():
    text = README.read_text().lower()
    for phrase in ("job-feedback.json", "synthetic", "replay", "expired", "operator-imported", "private", "cleanup"):
        assert phrase in text, f"README must state {phrase} boundary"
    assert "does not authenticate tokens cryptographically" in text
    assert "must not be exposed publicly" in text
