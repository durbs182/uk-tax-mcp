"""Prometheus metrics for the uk-tax-mcp HTTP API.

Imported by api.py; only meaningful when the `http` extra is installed.
All metrics use the `uk_tax_mcp_` namespace to avoid collisions.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

RULE_EXECUTIONS = Counter(
    "uk_tax_mcp_rule_executions_total",
    "Total rule executions via POST /v1/rules/{rule_id}/execute",
    ["rule_id", "jurisdiction", "tax_year", "status"],  # status: success | error
)

RULE_EXECUTION_SECONDS = Histogram(
    "uk_tax_mcp_rule_execution_seconds",
    "Wall-clock time spent evaluating a rule (excludes registry lookup)",
    ["rule_id", "jurisdiction", "tax_year"],
    buckets=[0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)
