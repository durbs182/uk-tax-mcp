"""Production HTTP API for uk-tax-mcp.

Versioned REST routes under /v1/ with structured error envelopes, CORS, and
auto-generated OpenAPI documentation at /v1/openapi.json.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hmrc_tax_mcp.ast.canonical import ast_checksum
from hmrc_tax_mcp.dsl.compiler import CompileError, compile_dsl
from hmrc_tax_mcp.evaluator import EvaluationError, Evaluator
from hmrc_tax_mcp.explainer import explain_rule as _explain_rule
from hmrc_tax_mcp.registry.store import get_rule, get_rule_snapshot, list_rules
from hmrc_tax_mcp.registry.tax_year import current_tax_year
from hmrc_tax_mcp.server import _DISCLAIMER
from hmrc_tax_mcp.validation.pipeline import validate_rule as _validate_rule

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="uk-tax-mcp",
    description="Deterministic HMRC tax rule engine — production HTTP API",
    version="1.0.0",
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce(data: Any) -> Any:
    """Convert Decimal values to strings so the result is JSON-serialisable."""

    def _default(o: object) -> str:
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError()

    return json.loads(json.dumps(data, default=_default))


def _err(code: str, message: str, status: int) -> HTTPException:
    """Raise an HTTPException with a structured error envelope."""
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    inputs: dict[str, Any]
    version: str | None = None
    jurisdiction: str | None = None
    tax_year: str | None = None
    trace: bool = False


class CompileRequest(BaseModel):
    dsl: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/v1/rules")
async def list_all_rules() -> dict[str, Any]:
    rules = list_rules()
    data = [
        {
            "rule_id": r.rule_id,
            "version": r.version,
            "title": r.title,
            "tax_year": r.tax_year,
            "jurisdiction": r.jurisdiction,
        }
        for r in rules
    ]
    return _coerce({
        "rules": data,
        "count": len(data),
        "disclaimer": _DISCLAIMER,
    })


@app.get("/v1/rules/{rule_id}")
async def get_rule_metadata(
    rule_id: str,
    version: str = Query(default="latest"),
    jurisdiction: str | None = Query(default=None),
    tax_year: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_year = tax_year or current_tax_year()
    try:
        rule = get_rule(rule_id, version, jurisdiction=jurisdiction, tax_year=resolved_year)
    except ValueError as exc:
        raise _err("invalid_request", str(exc), 400) from exc
    if rule is None:
        raise _err("rule_not_found", f"Rule {rule_id!r} not found", 404)
    result: dict[str, Any] = rule.model_dump(mode="json")
    result["disclaimer"] = _DISCLAIMER
    return _coerce(result)


@app.post("/v1/rules/{rule_id}/execute")
async def execute_rule(rule_id: str, req: ExecuteRequest) -> dict[str, Any]:
    version = req.version or "latest"
    resolved_year = req.tax_year or current_tax_year()
    try:
        rule = get_rule(
            rule_id, version, jurisdiction=req.jurisdiction, tax_year=resolved_year
        )
    except ValueError as exc:
        raise _err("invalid_request", str(exc), 400) from exc
    if rule is None:
        raise _err("rule_not_found", f"Rule {rule_id!r} not found", 404)

    evaluator = Evaluator(variables=req.inputs, trace=req.trace)
    try:
        output = evaluator.eval(rule.ast)
    except EvaluationError as exc:
        raise _err("evaluation_error", str(exc), 400) from exc

    response: dict[str, Any] = {
        "rule_id": rule.rule_id,
        "version": rule.version,
        "output": output,
        "checksum": rule.checksum,
        "disclaimer": _DISCLAIMER,
    }
    if req.trace:
        response["trace"] = [
            {"node": s.node, "inputs": s.inputs, "output": s.output}
            for s in evaluator.trace_steps
        ]
    return _coerce(response)


@app.get("/v1/rules/{rule_id}/explain")
async def explain_rule_endpoint(
    rule_id: str,
    version: str = Query(default="latest"),
    jurisdiction: str | None = Query(default=None),
    tax_year: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_year = tax_year or current_tax_year()
    try:
        rule = get_rule(rule_id, version, jurisdiction=jurisdiction, tax_year=resolved_year)
    except ValueError as exc:
        raise _err("invalid_request", str(exc), 400) from exc
    if rule is None:
        raise _err("rule_not_found", f"Rule {rule_id!r} not found", 404)

    try:
        explanation = _explain_rule(rule.model_dump(mode="json"))
    except Exception as exc:
        raise _err("explain_error", str(exc), 500) from exc

    explanation["disclaimer"] = _DISCLAIMER
    return _coerce(explanation)


@app.get("/v1/rules/{rule_id}/validate")
async def validate_rule_endpoint(
    rule_id: str,
    version: str = Query(default="latest"),
    jurisdiction: str | None = Query(default=None),
    tax_year: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved_year = tax_year or current_tax_year()
    try:
        rule = get_rule(rule_id, version, jurisdiction=jurisdiction, tax_year=resolved_year)
    except ValueError as exc:
        raise _err("invalid_request", str(exc), 400) from exc
    if rule is None:
        raise _err("rule_not_found", f"Rule {rule_id!r} not found", 404)

    results = _validate_rule(rule.model_dump())
    overall = all(r.passed for r in results)
    data: dict[str, Any] = {
        "rule_id": rule.rule_id,
        "version": rule.version,
        "overall": overall,
        "stages": [
            {
                "stage": r.stage.value,
                "passed": r.passed,
                "message": r.message,
                "details": r.details,
            }
            for r in results
        ],
        "disclaimer": _DISCLAIMER,
    }
    return _coerce(data)


@app.get("/v1/snapshots/{tax_year}/{jurisdiction}")
async def get_snapshot(tax_year: str, jurisdiction: str) -> dict[str, Any]:
    rules = get_rule_snapshot(tax_year, jurisdiction)
    return _coerce({
        "tax_year": tax_year,
        "jurisdiction": jurisdiction,
        "rules": [r.model_dump(mode="json") for r in rules],
        "count": len(rules),
        "disclaimer": _DISCLAIMER,
    })


@app.post("/v1/dsl/compile")
async def compile_dsl_endpoint(req: CompileRequest) -> dict[str, Any]:
    try:
        ast_node = compile_dsl(req.dsl)
    except CompileError as exc:
        raise _err("compile_error", str(exc), 400) from exc
    checksum = ast_checksum(ast_node)
    return _coerce({
        "ast": ast_node,
        "checksum": checksum,
        "disclaimer": _DISCLAIMER,
    })
