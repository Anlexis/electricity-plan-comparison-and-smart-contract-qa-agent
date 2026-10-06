"""Proof-of-Boundary — the real HTTP entry point.

Unit tests can only show that a node behaves when called correctly. These drive the ASGI
application the way a caller does, which is the only place several of these properties
are decidable at all: whether caller data reaches the pipeline, whether the declared
runtime configuration is actually consumed, and whether a refusal is legible to the
client rather than an opaque internal error.
"""

import importlib
import json
import pathlib

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

_CONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"

TOKEN = "pb-endpoint-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", TOKEN)
    import src.api.server as server

    importlib.reload(server)
    with TestClient(server.app) as test_client:
        yield test_client, server


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


class TestAuthBoundary:
    def test_missing_token_refused(self, client):
        test_client, _ = client
        response = test_client.post("/invoke", json={"input": "プラン比較"})
        assert response.status_code == 401

    def test_wrong_token_refused_without_revealing_why(self, client):
        test_client, _ = client
        response = test_client.post("/invoke", json={"input": "プラン比較"}, headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401
        assert TOKEN not in response.text

    def test_health_is_open(self, client):
        test_client, _ = client
        assert test_client.get("/health").status_code == 200


class TestCallerDataDoesRealWork:
    def test_answer_is_produced_from_caller_context(self, client):
        test_client, _ = client
        response = test_client.post(
            "/invoke",
            json={
                "input": "高圧電力プランを比較したい",
                "input_context": {"customer_type": "corporate", "contract_kw": 120},
            },
            headers=_auth(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("success", "SUCCESS")
        assert body["output"], "a validated request must produce a real answer"
        assert "出典" in body["output"]
        assert "高圧" in body["output"]

    def test_contract_capacity_changes_the_answer(self, client):
        """The same question with different capacities must not return the same text.

        This is the property that separates a pipeline that consumes caller data from
        one that merely accepts it: if both answers were identical, the field would be
        decorative regardless of how carefully it had been validated.
        """
        test_client, _ = client
        low = test_client.post(
            "/invoke",
            json={
                "input": "プランを比較したい",
                "input_context": {"customer_type": "corporate", "contract_kw": 10},
            },
            headers=_auth(),
        ).json()
        high = test_client.post(
            "/invoke",
            json={
                "input": "プランを比較したい",
                "input_context": {"customer_type": "corporate", "contract_kw": 500},
            },
            headers=_auth(),
        ).json()
        assert low["output"] != high["output"]
        assert "対象外" in low["output"], "a sub-threshold contract must be told the plan is unavailable"
        assert "選択可能" in high["output"]

    def test_usage_is_reported_as_a_band_never_as_money(self, client):
        test_client, _ = client
        body = test_client.post(
            "/invoke",
            json={"input": "プランを比較したい", "input_context": {"monthly_kwh": 450}},
            headers=_auth(),
        ).json()
        assert "使用量区分" in body["output"]
        # The standing boundary: aggregate comparison only, never a per-customer cost.
        assert "円" not in body["output"]

    def test_absent_context_degrades_to_the_baseline(self, client):
        test_client, _ = client
        body = test_client.post("/invoke", json={"input": "プランを比較したい"}, headers=_auth()).json()
        assert body["status"] in ("success", "SUCCESS")
        assert body["output"]


class TestValidationRejection:
    @pytest.mark.parametrize(
        "context",
        [
            {"contract_kw": "NaN"},
            {"contract_kw": "Infinity"},
            {"monthly_kwh": -1},
            {"monthly_kwh": 1e12},
            {"retailer": "'; DROP TABLE plans; --"},
            {"customer_type": "bogus"},
            {"plan_codes": ["NOT INERT"]},
            {"unknown_field": "x"},
        ],
    )
    def test_invalid_context_is_refused_through_the_api(self, client, context):
        test_client, _ = client
        body = test_client.post(
            "/invoke", json={"input": "プラン比較", "input_context": context}, headers=_auth()
        ).json()
        assert body["status"] in ("error", "ERROR")
        assert not body["output"], "a refused request must not return an answer"

    def test_rejected_values_are_not_echoed_back(self, client):
        test_client, _ = client
        marker = "zzz_rejected_marker_zzz"
        response = test_client.post(
            "/invoke",
            json={"input": "プラン比較", "input_context": {"retailer": marker}},
            headers=_auth(),
        )
        assert marker not in response.text

    def test_oversized_context_refused(self, client):
        test_client, _ = client
        response = test_client.post(
            "/invoke",
            json={"input": "プラン比較", "input_context": {"retailer": "x" * 300_000}},
            headers=_auth(),
        )
        assert response.status_code == 413


class TestCredentialShapedContext:
    """A credential-shaped context value must fail readably, not opaquely.

    The framework's first node returns the caller context verbatim in its result, and the
    output gate scans every returned value — so such a request dies inside the framework
    with a traceback before any template code runs. It cannot succeed either way, so the
    adapter converts it into a refusal that names the field.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "Bearer abcdefghij0123456789",
            "sk_live_" + "abcdefghij0123456789",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_credential_shaped_value_refused_with_a_named_field(self, client, value):
        test_client, _ = client
        response = test_client.post(
            "/invoke",
            json={"input": "プラン比較", "input_context": {"retailer": value}},
            headers=_auth(),
        )
        # 400, not 422: pydantic owns 422 and answers with a list of error objects there.
        assert response.status_code == 400
        assert "retailer" in response.text
        assert value not in response.text, "the refusal must not echo the value"

    def test_ordinary_domain_value_on_the_same_field_still_passes(self, client):
        test_client, _ = client
        response = test_client.post(
            "/invoke",
            json={"input": "プラン比較", "input_context": {"retailer": "newpower_energy"}},
            headers=_auth(),
        )
        assert response.status_code == 200


class TestRuntimeConfigIsConsumed:
    def test_declared_config_reaches_the_compiled_graph(self, client):
        """A declared runtime value must actually be in force, not merely declared.

        Loading the file is not the same as the graph consuming it: an agent constructed
        without config silently runs on framework defaults while the file still looks
        authoritative.
        """
        import yaml

        _, server = client
        declared = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        assert declared, "config/config.yaml must declare runtime parameters"
        for key, value in declared.items():
            assert server.agent.config.get(key) == value, f"{key} declared but not in force"

    def test_config_loader_degrades_when_the_file_is_unreadable(self, monkeypatch):
        from src.graph import graph as graph_module

        monkeypatch.setattr(graph_module, "_RUNTIME_CONFIG_PATH", graph_module.pathlib.Path("/nonexistent.yaml"))
        assert graph_module.load_runtime_config() == {}


class TestEnvelopeShape:
    def test_error_envelope_carries_no_traceback_or_source_paths(self, client):
        test_client, _ = client
        response = test_client.post(
            "/invoke",
            json={"input": "プラン比較", "input_context": {"contract_kw": "NaN"}},
            headers=_auth(),
        )
        text = response.text
        assert "Traceback" not in text
        assert "/src/" not in text
        assert json.loads(text)["output"] in (None, "")
