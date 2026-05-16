"""Lightweight FastAPI wrapper for local development.

Provides a single `/call` endpoint to invoke the same internal functions
the MCP stdio server exposes.
This file is intended for local dev and testing only (not for production).
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_DISCLAIMER = "This is a deterministic calculation, not tax advice."

app = FastAPI()

# Safe JSON helper to convert Decimal -> str for JSON transport
def _json_serializable(data: Any) -> Any:
    def default(o: object) -> str:
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError()
    return json.loads(json.dumps(data, default=default))

# Try to import internal modules; fail gracefully with informative errors
try:
    from hmrc_tax_mcp.registry.store import get_rule, get_rule_snapshot, list_rules
except Exception as exc:  # pragma: no cover - environment dependent
    list_rules = None  # type: ignore[assignment]
    get_rule = None  # type: ignore[assignment]
    get_rule_snapshot = None  # type: ignore[assignment]
    _REGISTRY_IMPORT_ERROR: Exception | None = exc
else:
    _REGISTRY_IMPORT_ERROR = None

try:
    from hmrc_tax_mcp.evaluator import EvaluationError, Evaluator
except Exception:
    Evaluator = None  # type: ignore[assignment, misc]
    EvaluationError = Exception  # type: ignore[assignment, misc]

try:
    from hmrc_tax_mcp.dsl.compiler import compile_dsl as compile_dsl_internal
except Exception:
    compile_dsl_internal = None  # type: ignore[assignment]

try:
    from hmrc_tax_mcp.validation.pipeline import validate_rule as validate_rule_internal
except Exception:
    validate_rule_internal = None  # type: ignore[assignment]

try:
    # optional explainer; if not present, fallback
    from hmrc_tax_mcp.explainer import explain_rule as explain_rule_internal
except Exception:
    explain_rule_internal = None  # type: ignore[assignment]

class CallReq(BaseModel):
    name: str
    arguments: dict[str, Any] | None = None

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}

@app.post("/call")
async def call_tool(req: CallReq) -> Any:
    name = req.name
    arguments = req.arguments or {}

    if name == "list_rules":
        if list_rules is None:
            raise HTTPException(
                status_code=500,
                detail=f"registry not available: {_REGISTRY_IMPORT_ERROR}",
            )
        rules = list_rules()
        data = [
            {
                "rule_id": r.rule_id,
                "version": r.version,
                "title": getattr(r, "title", None),
                "tax_year": getattr(r, "tax_year", None),
                "jurisdiction": getattr(r, "jurisdiction", None),
            }
            for r in rules
        ]
        return _json_serializable(data)

    if name == "get_rule":
        if get_rule is None:
            raise HTTPException(status_code=500, detail="registry.get_rule not available")
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        # pydantic model -> dict
        try:
            return _json_serializable(rule.model_dump(mode="json"))
        except Exception:
            return _json_serializable(rule)

    if name == "execute_rule":
        if get_rule is None or Evaluator is None:
            raise HTTPException(
                status_code=500,
                detail="runtime: registry or evaluator not available",
            )
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")

        inputs = arguments.get("inputs", {})
        trace_flag = bool(arguments.get("trace", False))
        evaluator = Evaluator(variables=inputs, trace=trace_flag)
        try:
            output = evaluator.eval(rule.ast)
        except EvaluationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        response: dict[str, Any] = {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "output": output,
            "disclaimer": _DISCLAIMER,
        }
        if trace_flag:
            # attempt to return trace steps if available
            trace = getattr(evaluator, "trace_steps", None)
            if trace is not None:
                response["trace"] = trace
        return _json_serializable(response)

    if name == "compile_dsl":
        if compile_dsl_internal is None:
            raise HTTPException(status_code=500, detail="compile_dsl not available")
        try:
            ast_node = compile_dsl_internal(arguments.get("dsl", ""))
            return _json_serializable({"ast": ast_node})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if name == "validate_rule":
        if validate_rule_internal is None or get_rule is None:
            raise HTTPException(status_code=500, detail="validation or registry not available")
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")

        results = validate_rule_internal(rule.model_dump())
        overall = all(r.passed for r in results)
        validate_data: dict[str, Any] = {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "overall": overall,
            "stages": [
                {
                    "stage": getattr(r, "stage", None),
                    "passed": getattr(r, "passed", False),
                    "message": getattr(r, "message", None),
                }
                for r in results
            ],
        }
        return _json_serializable(validate_data)

    if name == "explain_rule":
        if explain_rule_internal is None:
            raise HTTPException(status_code=500, detail="explain_rule not available")
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        try:
            explanation = explain_rule_internal(rule.model_dump())
            if isinstance(explanation, dict):
                explanation["disclaimer"] = _DISCLAIMER
            return _json_serializable(explanation)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    if name == "tax_get_rule_snapshot":
        if get_rule_snapshot is None:
            raise HTTPException(status_code=500, detail="get_rule_snapshot not available")
        tax_year = str(arguments.get("tax_year", ""))
        jurisdiction = str(arguments.get("jurisdiction", ""))
        rules = get_rule_snapshot(tax_year, jurisdiction)
        return _json_serializable({
            "tax_year": tax_year,
            "jurisdiction": jurisdiction,
            "rules": [r.model_dump(mode="json") for r in rules],
        })

    raise HTTPException(status_code=400, detail=f"Unknown tool: {name}")
