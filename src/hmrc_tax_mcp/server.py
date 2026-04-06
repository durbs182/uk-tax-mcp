"""
HMRC Tax MCP Server — stdio transport.

Exposes deterministic HMRC tax rule tools to AI agents via the Model Context Protocol.
LLMs orchestrate and explain; this server computes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from hmrc_tax_mcp.ast.canonical import ast_checksum
from hmrc_tax_mcp.dsl.compiler import CompileError
from hmrc_tax_mcp.dsl.compiler import compile_dsl as _compile_dsl
from hmrc_tax_mcp.evaluator import EvaluationError, Evaluator
from hmrc_tax_mcp.explainer import explain_rule as _explain_rule
from hmrc_tax_mcp.extractor.nl_extractor import NLExtractor
from hmrc_tax_mcp.registry.store import get_rule, get_rule_snapshot, list_rules
from hmrc_tax_mcp.registry.tax_year import current_tax_year
from hmrc_tax_mcp.validation.pipeline import validate_rule as _validate_rule

MCPServer: Any = None
MCPStdioServer: Any = None
MCPTextContent: Any = None
MCPTool: Any = None

try:
    from mcp.server import Server as _MCPServerImported
    from mcp.server.stdio import stdio_server as _MCPStdioServerImported
    from mcp.types import TextContent as _MCPTextContentImported
    from mcp.types import Tool as _MCPToolImported
    MCPServer = _MCPServerImported
    MCPStdioServer = _MCPStdioServerImported
    MCPTextContent = _MCPTextContentImported
    MCPTool = _MCPToolImported
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

if _MCP_AVAILABLE:
    TextContent = MCPTextContent
    Tool = MCPTool
else:
    @dataclass
    class TextContent:  # type: ignore[no-redef]
        type: str
        text: str

    @dataclass
    class Tool:  # type: ignore[no-redef]
        name: str
        description: str
        inputSchema: dict[str, Any]

app = MCPServer("hmrc-tax-mcp") if _MCP_AVAILABLE else None


def _json(data: Any) -> str:
    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")

    return json.dumps(data, default=_default, indent=2)


async def handle_list_tools() -> list[Any]:
    return [
        Tool(
            name="list_rules",
            description="List all available HMRC tax rule IDs and versions.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_rule",
            description="Get DSL source, AST, and metadata for a specific rule.",
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string", "description": "e.g. 'pa.taper.2025-26'"},
                    "version": {
                        "type": "string",
                        "description": "Semver or 'latest'",
                        "default": "latest",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "description": "Defaults to 'rUK'. Pass 'scotland' for Scottish tax rules.",
                    },
                    "tax_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-26'. Defaults to the current UK tax year "
                            "(6 Apr – 5 Apr) when omitted."
                        ),
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="execute_rule",
            description=(
                "Execute a tax rule with given inputs and return the result. "
                "All arithmetic uses decimal.Decimal for precision."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "version": {"type": "string", "default": "latest"},
                    "jurisdiction": {
                        "type": "string",
                        "description": "Defaults to 'rUK'. Pass 'scotland' for Scottish tax rules.",
                    },
                    "tax_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-26'. Defaults to the current UK tax year "
                            "(6 Apr – 5 Apr) when omitted."
                        ),
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Variable bindings, e.g. {'adjusted_net_income': 110000}",
                    },
                    "trace": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, include a full execution trace in the response.",
                    },
                },
                "required": ["rule_id", "inputs"],
            },
        ),
        Tool(
            name="tax_get_rule_snapshot",
            description="Return the complete rule set for a given tax year and jurisdiction.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tax_year": {"type": "string", "description": "e.g. '2025-26'"},
                    "jurisdiction": {"type": "string", "description": "'rUK' or 'scotland'"},
                },
                "required": ["tax_year", "jurisdiction"],
            },
        ),
        Tool(
            name="compile_dsl",
            description=(
                "Compile DSL text to a canonical AST, returning the AST and its SHA-256 checksum."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dsl": {"type": "string", "description": "DSL source text"},
                },
                "required": ["dsl"],
            },
        ),
        Tool(
            name="validate_rule",
            description=(
                "Run the 6-stage validation pipeline on a rule (by ID or as a raw dict). "
                "Returns pass/fail per stage: syntax, semantic, canonicalisation, "
                "execution, worked_examples, human_review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "Rule ID to validate (e.g. 'income_tax_bands')",
                    },
                    "version": {"type": "string", "default": "latest"},
                    "jurisdiction": {
                        "type": "string",
                        "description": "Defaults to 'rUK'. Pass 'scotland' for Scottish tax rules.",
                    },
                    "tax_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-26'. Defaults to the current UK tax year "
                            "(6 Apr – 5 Apr) when omitted."
                        ),
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="explain_rule",
            description=(
                "Return a structured, human-readable explanation of a tax rule: "
                "what it computes, what input variables it needs, HMRC citations, "
                "and the authoritative DSL source text."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string",
                        "description": "Rule ID (e.g. 'income_tax_bands')",
                    },
                    "version": {"type": "string", "default": "latest"},
                    "jurisdiction": {
                        "type": "string",
                        "description": "Defaults to 'rUK'. Pass 'scotland' for Scottish tax rules.",
                    },
                    "tax_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-26'. Defaults to the current UK tax year "
                            "(6 Apr – 5 Apr) when omitted."
                        ),
                    },
                },
                "required": ["rule_id"],
            },
        ),
        Tool(
            name="trace_execution",
            description=(
                "Execute a tax rule and return a full step-by-step audit trace showing "
                "every node evaluated, its inputs, and its output. Useful for explaining "
                "how a specific result was reached."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "version": {"type": "string", "default": "latest"},
                    "jurisdiction": {
                        "type": "string",
                        "description": "Defaults to 'rUK'. Pass 'scotland' for Scottish tax rules.",
                    },
                    "tax_year": {
                        "type": "string",
                        "description": (
                            "e.g. '2025-26'. Defaults to the current UK tax year "
                            "(6 Apr – 5 Apr) when omitted."
                        ),
                    },
                    "inputs": {
                        "type": "object",
                        "description": "Variable bindings, e.g. {'taxable_income': 50000}",
                    },
                },
                "required": ["rule_id", "inputs"],
            },
        ),
        Tool(
            name="extract_rule",
            description=(
                "Submit HMRC legislative prose to Claude and receive a draft DSL rule. "
                "The result is ALWAYS marked unreviewed — it must be validated by a human "
                "engineer before it can be published to the rule registry. "
                "Requires the ANTHROPIC_API_KEY environment variable."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hmrc_text": {
                        "type": "string",
                        "description": "Verbatim HMRC legislative text to convert to DSL.",
                    },
                    "model": {
                        "type": "string",
                        "default": "claude-3-5-haiku-20241022",
                        "description": "Anthropic model to use for extraction.",
                    },
                },
                "required": ["hmrc_text"],
            },
        ),
    ]


async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
    if name == "list_rules":
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
        return [TextContent(type="text", text=_json(data))]

    if name == "get_rule":
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year") or current_tax_year(),
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        if rule is None:
            return [TextContent(type="text", text=_json({"error": "Rule not found"}))]
        return [TextContent(type="text", text=_json(rule.model_dump(mode="json")))]

    if name == "execute_rule":
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year") or current_tax_year(),
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        if rule is None:
            return [TextContent(type="text", text=_json({"error": "Rule not found"}))]
        evaluator = Evaluator(
            variables=arguments.get("inputs", {}),
            trace=arguments.get("trace", False),
        )
        try:
            output = evaluator.eval(rule.ast)
        except EvaluationError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]

        response: dict[str, Any] = {
            "rule_id": rule.rule_id,
            "version": rule.version,
            "output": output,
            "checksum": rule.checksum,
        }
        if arguments.get("trace"):
            response["trace"] = [
                {"node": s.node, "inputs": s.inputs, "output": s.output}
                for s in evaluator.trace_steps
            ]
        return [TextContent(type="text", text=_json(response))]

    if name == "tax_get_rule_snapshot":
        rules = get_rule_snapshot(arguments["tax_year"], arguments["jurisdiction"])
        data = {  # type: ignore[assignment]
            "tax_year": arguments["tax_year"],
            "jurisdiction": arguments["jurisdiction"],
            "rules": [r.model_dump(mode="json") for r in rules],
        }
        return [TextContent(type="text", text=_json(data))]

    if name == "compile_dsl":
        dsl_src = arguments.get("dsl", "")
        try:
            ast_node = _compile_dsl(dsl_src)
            checksum = ast_checksum(ast_node)
            return [TextContent(type="text", text=_json({"ast": ast_node, "checksum": checksum}))]
        except CompileError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]

    if name == "validate_rule":
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year") or current_tax_year(),
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        if rule is None:
            return [TextContent(type="text", text=_json({"error": "Rule not found"}))]
        results = _validate_rule(rule.model_dump())
        data = {  # type: ignore[assignment]
            "rule_id": rule.rule_id,
            "version": rule.version,
            "stages": [
                {
                    "stage": r.stage.value,
                    "passed": r.passed,
                    "message": r.message,
                    "details": r.details,
                }
                for r in results
            ],
            "overall": all(r.passed for r in results),
        }
        return [TextContent(type="text", text=_json(data))]

    if name == "explain_rule":
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year") or current_tax_year(),
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        if rule is None:
            return [TextContent(type="text", text=_json({"error": "Rule not found"}))]
        explanation = _explain_rule(rule.model_dump(mode="json"))
        return [TextContent(type="text", text=_json(explanation))]

    if name == "trace_execution":
        try:
            rule = get_rule(
                arguments["rule_id"],
                arguments.get("version", "latest"),
                jurisdiction=arguments.get("jurisdiction"),
                tax_year=arguments.get("tax_year") or current_tax_year(),
            )
        except ValueError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        if rule is None:
            return [TextContent(type="text", text=_json({"error": "Rule not found"}))]
        evaluator = Evaluator(
            variables=arguments.get("inputs", {}),
            trace=True,
        )
        try:
            output = evaluator.eval(rule.ast)
        except EvaluationError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        data = {  # type: ignore[assignment]
            "rule_id": rule.rule_id,
            "version": rule.version,
            "inputs": arguments.get("inputs", {}),
            "output": output,
            "checksum": rule.checksum,
            "steps": len(evaluator.trace_steps),
            "trace": [
                {
                    "step": i + 1,
                    "node": s.node,
                    "inputs": s.inputs,
                    "output": s.output,
                }
                for i, s in enumerate(evaluator.trace_steps)
            ],
        }
        return [TextContent(type="text", text=_json(data))]

    if name == "extract_rule":
        hmrc_text = arguments.get("hmrc_text", "")
        model = arguments.get("model", "claude-3-5-haiku-20241022")
        try:
            extractor = NLExtractor(model=model)
            result = extractor.extract(hmrc_text)
        except ImportError as exc:
            return [TextContent(type="text", text=_json({"error": str(exc)}))]
        except Exception as exc:  # noqa: BLE001
            return [TextContent(type="text", text=_json({"error": f"Extraction failed: {exc}"}))]

        # Attempt to compile the DSL and compute checksum
        compile_error: str | None = None
        draft_checksum: str | None = None
        draft_ast: dict[str, Any] | None = None
        try:
            draft_ast = _compile_dsl(result.dsl_source)
            draft_checksum = ast_checksum(draft_ast)
        except CompileError as exc:
            compile_error = str(exc)

        data = {  # type: ignore[assignment]
            "draft": result.to_registry_dict(),
            "dsl_source": result.dsl_source,
            "checksum": draft_checksum,
            "ast": draft_ast,
            "compile_error": compile_error,
            "warnings": result.warnings,
            "requires_review": result.requires_review,
            "review_instructions": (
                "This rule was generated by an LLM and has NOT been verified. "
                "You MUST check every value against the original HMRC source before "
                "adding it to the registry. Set reviewed_by to your name/email when done."
            ),
        }
        return [TextContent(type="text", text=_json(data))]

    return [TextContent(type="text", text=_json({"error": f"Unknown tool: {name!r}"}))]


if _MCP_AVAILABLE and app is not None:
    app_runtime = cast(Any, app)
    handle_list_tools = app_runtime.list_tools()(handle_list_tools)
    handle_call_tool = app_runtime.call_tool()(handle_call_tool)


async def _run() -> None:
    if MCPStdioServer is None or app is None:
        raise RuntimeError(
            "MCP server requires Python >=3.10. "
            "Install with: pip install 'hmrc-tax-mcp[server]'"
        )
    async with MCPStdioServer() as (read_stream, write_stream):
        await app.run(
            read_stream, write_stream, app.create_initialization_options()
        )


def main() -> None:
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "MCP server requires Python >=3.10. "
            "Install with: pip install 'hmrc-tax-mcp[server]'"
        )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
