"""Tests for the production HTTP API (api.py)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hmrc_tax_mcp.api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# F3 — Health / liveness / readiness probes
# ---------------------------------------------------------------------------

class TestHealthEndpoints:
    def test_health_returns_200(self) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_liveness_returns_200(self) -> None:
        r = client.get("/healthz/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_readiness_returns_200_when_rules_loaded(self) -> None:
        r = client.get("/healthz/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["rule_count"] > 0

    def test_readiness_503_when_registry_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hmrc_tax_mcp import api as api_module
        monkeypatch.setattr(api_module, "list_rules", lambda: [])
        r = client.get("/healthz/ready")
        assert r.status_code == 503
        assert r.json()["detail"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# F5 — Prometheus metrics endpoint and per-rule instrumentation
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_metrics_returns_200_with_prometheus_content_type(self) -> None:
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]

    def test_metrics_contains_default_process_metrics(self) -> None:
        r = client.get("/metrics")
        # prometheus_client always exposes python_info
        assert "python_info" in r.text

    def test_execute_increments_counter(self) -> None:
        from prometheus_client import REGISTRY

        def _sample(metric_name: str, labels: dict[str, str]) -> float | None:
            for metric in REGISTRY.collect():
                if metric.name == metric_name:
                    for sample in metric.samples:
                        if sample.name == metric_name + "_total":
                            if all(sample.labels.get(k) == v for k, v in labels.items()):
                                return sample.value
            return None

        before = (
            _sample("uk_tax_mcp_rule_executions", {"rule_id": "pa_taper", "status": "success"}
            ) or 0.0
        )

        r = client.post(
            "/v1/rules/pa_taper/execute",
            json={"inputs": {"adjusted_net_income": 110000}},
        )
        assert r.status_code == 200

        after = (
            _sample("uk_tax_mcp_rule_executions", {"rule_id": "pa_taper", "status": "success"}
            ) or 0.0
        )
        assert after == before + 1.0

    def test_execute_error_increments_error_counter(self) -> None:
        from prometheus_client import REGISTRY

        def _sample(metric_name: str, labels: dict[str, str]) -> float | None:
            for metric in REGISTRY.collect():
                if metric.name == metric_name:
                    for sample in metric.samples:
                        if sample.name == metric_name + "_total":
                            if all(sample.labels.get(k) == v for k, v in labels.items()):
                                return sample.value
            return None

        before = (
            _sample("uk_tax_mcp_rule_executions", {"rule_id": "pa_taper", "status": "error"}) or 0.0
        )

        # missing required input → EvaluationError → status=error
        r = client.post("/v1/rules/pa_taper/execute", json={"inputs": {}})
        assert r.status_code == 400

        after = (
            _sample("uk_tax_mcp_rule_executions", {"rule_id": "pa_taper", "status": "error"}) or 0.0
        )
        assert after == before + 1.0

    def test_metrics_output_contains_rule_executions_after_call(self) -> None:
        client.post(
            "/v1/rules/cgt_exempt/execute",
            json={"inputs": {}},
        )
        r = client.get("/metrics")
        assert "uk_tax_mcp_rule_executions_total" in r.text
        assert "uk_tax_mcp_rule_execution_seconds" in r.text
