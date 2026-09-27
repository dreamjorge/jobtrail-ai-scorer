from copy import deepcopy
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "validation" / "n8n" / "docker-compose.yml"
README = ROOT / "validation" / "n8n" / "README.md"

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
