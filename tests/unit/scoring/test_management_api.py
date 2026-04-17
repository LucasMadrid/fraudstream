"""Tests for the fraud rule management FastAPI app."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

import pipelines.scoring.management_api as api_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_RULE_YAML = [
    {
        "rule_id": "VEL-001",
        "name": "HIGH_FREQ",
        "family": "velocity",
        "severity": "high",
        "enabled": True,
        "mode": "active",
        "conditions": {"field": "vel_count_1m", "count": 5},
    },
    {
        "rule_id": "VEL-SHADOW",
        "name": "SHADOW_RULE",
        "family": "velocity",
        "severity": "medium",
        "enabled": True,
        "mode": "shadow",
        "conditions": {"field": "vel_count_5m", "count": 10},
    },
]


@pytest.fixture()
def rules_yaml(tmp_path):
    """Write minimal rules YAML and return its path."""
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.dump(_MINIMAL_RULE_YAML))
    return str(path)


@pytest.fixture()
def client(rules_yaml, monkeypatch):
    """TestClient with rules pre-loaded directly (bypasses lifespan startup)."""
    monkeypatch.setenv("RULES_YAML_PATH", rules_yaml)

    # Directly load rules and config to bypass lifespan async context
    from pipelines.scoring.config import ScoringConfig

    api_module._config = ScoringConfig(rules_yaml_path=rules_yaml)
    api_module._load_rules_from_yaml(rules_yaml)
    api_module._circuit_breaker = None

    with TestClient(api_module.app, raise_server_exceptions=True) as c:
        yield c

    # Cleanup
    api_module._rules_dict = {}
    api_module._config = None
    api_module._circuit_breaker = None


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_returns_ok(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /circuit-breaker/state
# ---------------------------------------------------------------------------


class TestCircuitBreakerState:
    def test_no_cb_returns_unknown(self, client):
        api_module._circuit_breaker = None
        resp = client.get("/circuit-breaker/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "unknown"
        assert data["failure_count"] == 0

    def test_with_cb_returns_state(self, client):
        from pipelines.scoring.circuit_breaker import MLCircuitBreaker
        from pipelines.scoring.config import ScoringConfig
        from pipelines.scoring.ml_client import StubMLModelClient

        cfg = ScoringConfig()
        stub = StubMLModelClient()
        cb = MLCircuitBreaker(stub, cfg)
        api_module._circuit_breaker = cb

        resp = client.get("/circuit-breaker/state")
        assert resp.status_code == 200
        assert resp.json()["state"] == "closed"
        api_module._circuit_breaker = None


# ---------------------------------------------------------------------------
# POST /rules/{rule_id}/demote
# ---------------------------------------------------------------------------


class TestDemoteRule:
    def test_demote_active_rule(self, client):
        resp = client.post("/rules/VEL-001/demote")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rule_id"] == "VEL-001"
        assert data["previous_mode"] == "active"
        assert data["new_mode"] == "shadow"

    def test_demote_already_shadow_returns_409(self, client):
        resp = client.post("/rules/VEL-SHADOW/demote")
        assert resp.status_code == 409

    def test_demote_unknown_rule_returns_404(self, client):
        resp = client.post("/rules/NONEXISTENT/demote")
        assert resp.status_code == 404

    def test_demote_persists_to_yaml(self, client, rules_yaml):
        client.post("/rules/VEL-001/demote")
        with open(rules_yaml) as f:
            data = yaml.safe_load(f)
        vel001 = next(r for r in data if r["rule_id"] == "VEL-001")
        assert vel001["mode"] == "shadow"


# ---------------------------------------------------------------------------
# POST /rules/{rule_id}/promote
# ---------------------------------------------------------------------------


class TestPromoteRule:
    def test_promote_shadow_rule(self, client):
        resp = client.post("/rules/VEL-SHADOW/promote")
        assert resp.status_code == 200
        data = resp.json()
        assert data["rule_id"] == "VEL-SHADOW"
        assert data["previous_mode"] == "shadow"
        assert data["new_mode"] == "active"

    def test_promote_already_active_returns_409(self, client):
        resp = client.post("/rules/VEL-001/promote")
        assert resp.status_code == 409

    def test_promote_unknown_rule_returns_404(self, client):
        resp = client.post("/rules/DOES_NOT_EXIST/promote")
        assert resp.status_code == 404

    def test_promote_persists_to_yaml(self, client, rules_yaml):
        client.post("/rules/VEL-SHADOW/promote")
        with open(rules_yaml) as f:
            data = yaml.safe_load(f)
        shadow = next(r for r in data if r["rule_id"] == "VEL-SHADOW")
        assert shadow["mode"] == "active"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestLoadRulesFromYaml:
    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            api_module._load_rules_from_yaml("/nonexistent/path/rules.yaml")

    def test_non_list_yaml_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("key: value")
        with pytest.raises(ValueError, match="must be a YAML list"):
            api_module._load_rules_from_yaml(str(p))


# ---------------------------------------------------------------------------
# Security: API key authentication
# ---------------------------------------------------------------------------


class TestApiKeyAuth:
    def test_missing_key_returns_401_when_env_set(self, client, monkeypatch):
        monkeypatch.setenv("MANAGEMENT_API_KEY", "secret-token")
        resp = client.post("/rules/VEL-001/demote")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client, monkeypatch):
        monkeypatch.setenv("MANAGEMENT_API_KEY", "secret-token")
        resp = client.post("/rules/VEL-001/demote", headers={"X-Api-Key": "wrong"})
        assert resp.status_code == 401

    def test_correct_key_is_accepted(self, client, monkeypatch):
        monkeypatch.setenv("MANAGEMENT_API_KEY", "secret-token")
        resp = client.post("/rules/VEL-001/demote", headers={"X-Api-Key": "secret-token"})
        assert resp.status_code == 200

    def test_no_env_key_allows_unauthenticated(self, client, monkeypatch):
        monkeypatch.delenv("MANAGEMENT_API_KEY", raising=False)
        resp = client.post("/rules/VEL-001/demote")
        assert resp.status_code == 200

    def test_cb_endpoint_requires_key_when_set(self, client, monkeypatch):
        monkeypatch.setenv("MANAGEMENT_API_KEY", "secret-token")
        resp = client.get("/circuit-breaker/state")
        assert resp.status_code == 401

    def test_healthz_never_requires_key(self, client, monkeypatch):
        monkeypatch.setenv("MANAGEMENT_API_KEY", "secret-token")
        resp = client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Security: rule_id pattern validation
# ---------------------------------------------------------------------------


class TestRuleIdPatternValidation:
    def test_valid_rule_id_accepted(self, client):
        resp = client.post("/rules/VEL-001/demote")
        assert resp.status_code == 200

    def test_rule_id_with_special_chars_returns_422(self, client):
        resp = client.post("/rules/bad!rule/demote")
        assert resp.status_code == 422

    def test_rule_id_starting_with_hyphen_returns_422(self, client):
        resp = client.post("/rules/-bad/demote")
        assert resp.status_code == 422

    def test_rule_id_with_spaces_returns_422(self, client):
        resp = client.post("/rules/bad rule/demote")
        assert resp.status_code == 422

    def test_single_char_rule_id_returns_422(self, client):
        resp = client.post("/rules/a/demote")
        assert resp.status_code == 422

    def test_65_char_rule_id_returns_422(self, client):
        rule_id = "a" * 65
        resp = client.post(f"/rules/{rule_id}/demote")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Security: response headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_healthz_has_x_content_type_options(self, client):
        resp = client.get("/healthz")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_healthz_has_x_frame_options(self, client):
        resp = client.get("/healthz")
        assert resp.headers.get("x-frame-options") == "DENY"

    def test_healthz_has_csp(self, client):
        resp = client.get("/healthz")
        assert resp.headers.get("content-security-policy") == "default-src 'none'"

    def test_demote_response_has_security_headers(self, client):
        resp = client.post("/rules/VEL-001/demote")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
