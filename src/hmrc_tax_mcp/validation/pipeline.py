"""
6-stage validation pipeline for HMRC tax rules.

Stages:
  1. Syntax          — DSL parses without error
  2. Semantic        — required fields present, types correct
  3. Canonicalisation — AST checksum matches stored checksum
  4. Execution        — rule evaluates without error on smoke-test inputs
  5. Worked examples  — outputs match HMRC-published examples (if provided)
  6. Human review     — rule has a non-null reviewed_by field

Each stage receives the full rule dict (as loaded from YAML / supplied by
caller). Stages run in order; if any stage fails, remaining stages are skipped
and returned as SKIPPED results.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from hmrc_tax_mcp.ast.canonical import ast_checksum as _compute_ast_checksum
from hmrc_tax_mcp.dsl.compiler import CompileError, compile_dsl
from hmrc_tax_mcp.evaluator import EvaluationError, Evaluator

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {
    "rule_id", "version", "tax_year", "jurisdiction",
    "title", "description", "dsl_source", "ast", "checksum",
    "citations", "provenance", "published_at",
}

_VALID_PROVENANCES = {"manual", "nl_extracted", "migrated"}

# D3: at least one citation label must reference a known legislative source.
# Matches statute abbreviations (ITEPA 2003), spelled-out Act references
# (Finance Act 2016, Pensions Act 2014), Statutory Instruments (SI 2006/572),
# Finance Bills, HMRC manual codes (CG18000, IHTM12345, SAIM2050, SDLTM09845),
# and LBTT (Scottish stamp duty).
_LEGISLATIVE_REF_RE = re.compile(
    # Common statute abbreviations
    r"ITEPA\s+\d{4}"
    r"|TCGA\s+\d{4}"
    r"|IHTA\s+\d{4}"
    r"|ITA\s+\d{4}"
    r"|ITTOIA\s+\d{4}"
    r"|FA\s+\d{4}"
    # Spelled-out "X Act YYYY" references (Finance Act 2016, Pensions Act 2014, Care Act 2014 …)
    r"|Act\s+\d{4}"
    # Finance Bills (pre-Royal-Assent references are legitimate)
    r"|Finance Bill"
    # Statutory Instruments — SI YYYY/NNN
    r"|SI\s+\d{4}/\d+"
    # HMRC manual codes
    r"|IHTM\d+"
    r"|CG\d+"
    r"|PTM\d+"
    r"|EIM\d+"
    r"|HS\d+"
    r"|SAIM\d+"
    r"|SDLTM\d+"
    r"|NIM\d+"
    # Scottish stamp duty
    r"|LBTT"
)


def _is_literal_two(arg: Any) -> bool:
    """Return True if the AST node or value represents the numeric literal 2."""
    # Handle plain Python numeric values directly.
    if isinstance(arg, (int, float, Decimal)):
        try:
            return Decimal(str(arg)) == Decimal("2")
        except Exception:
            return False

    # Handle common AST literal encodings, e.g. {"node": "CONST", "value": 2}.
    if isinstance(arg, dict):
        if arg.get("node") in {"CONST", "INT", "NUMBER", "DECIMAL", "LITERAL"}:
            value = arg.get("value")
            if isinstance(value, (int, float, Decimal)):
                try:
                    return Decimal(str(value)) == Decimal("2")
                except Exception:
                    return False
            if isinstance(value, str):
                try:
                    return Decimal(value) == Decimal("2")
                except Exception:
                    return False

    return False


def _final_result_is_rounded(ast: Any) -> bool:
    """Return True if the effective result of the AST is wrapped in round(expr, 2).

    Traverses LET bodies and IF branches so that wrapping patterns like
    ``let x = … in round(x, 2)`` are recognised. For IF nodes, both branches
    must be rounded to two decimal places.
    """
    if not isinstance(ast, dict):
        return False
    node = ast.get("node")
    if node == "CALL" and ast.get("name") == "round":
        # Enforce round(expr, 2): exactly two arguments and second is literal 2.
        args = ast.get("args") or ast.get("arguments")
        if isinstance(args, list) and len(args) == 2 and _is_literal_two(args[1]):
            return True
        return False
    if node == "LET":
        return _final_result_is_rounded(ast.get("body"))
    if node == "IF":
        return (
            _final_result_is_rounded(ast.get("then"))
            and _final_result_is_rounded(ast.get("else") or ast.get("else_"))
        )
    return False


class ValidationStage(str, Enum):
    SYNTAX = "syntax"
    SEMANTIC = "semantic"
    CANONICALISATION = "canonicalisation"
    EXECUTION = "execution"
    WORKED_EXAMPLES = "worked_examples"
    HUMAN_REVIEW = "human_review"


@dataclass
class ValidationResult:
    stage: ValidationStage
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def skipped(self) -> bool:
        return not self.passed and self.message.startswith("SKIPPED")


@dataclass
class WorkedExample:
    """A single input→expected-output pair used for stage-5 checks."""
    description: str
    inputs: dict[str, Any]
    expected: Any  # numeric or bool — compared with Decimal precision
    tolerance: str = "0"  # absolute Decimal tolerance (default: exact)
    source: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _skip(stage: ValidationStage, reason: str) -> ValidationResult:
    return ValidationResult(stage=stage, passed=False, message=f"SKIPPED: {reason}")


def _to_decimal(value: Any) -> Decimal:
    """Convert a Python value (int, float, bool, str, Decimal) to Decimal."""
    if isinstance(value, bool):
        return Decimal(1) if value else Decimal(0)
    return Decimal(str(value))


def _repo_root() -> Path:
    """Return the repository root when running from a source checkout."""
    return Path(__file__).resolve().parents[3]


def _default_worked_examples_path(rule: dict[str, Any]) -> Path | None:
    tax_year = rule.get("tax_year")
    jurisdiction = str(rule.get("jurisdiction", "")).lower()
    rule_id = rule.get("rule_id")
    if not tax_year or not jurisdiction or not rule_id:
        return None
    path = (
        _repo_root()
        / "tests"
        / "worked_examples"
        / str(tax_year)
        / jurisdiction
        / f"{rule_id}.yaml"
    )
    return path if path.exists() else None


def _worked_examples_required(rule: dict[str, Any]) -> bool:
    """High-impact rules must carry worked examples before they can pass stage 5."""
    return bool(rule.get("monetary_output") or rule.get("reviewed_by"))


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------

def _stage_syntax(rule: dict[str, Any]) -> ValidationResult:
    """Stage 1: DSL source must parse and compile to an AST without error."""
    dsl_source = rule.get("dsl_source", "")
    if not dsl_source or not dsl_source.strip():
        return ValidationResult(
            stage=ValidationStage.SYNTAX,
            passed=False,
            message="dsl_source is empty",
        )
    try:
        compile_dsl(dsl_source)
    except CompileError as exc:
        return ValidationResult(
            stage=ValidationStage.SYNTAX,
            passed=False,
            message=f"DSL compilation error: {exc}",
        )
    return ValidationResult(
        stage=ValidationStage.SYNTAX,
        passed=True,
        message="DSL source compiles successfully",
    )


def _stage_semantic(rule: dict[str, Any]) -> ValidationResult:
    """Stage 2: Required fields present; provenance valid; citations non-empty."""
    missing = _REQUIRED_FIELDS - set(rule.keys())
    if missing:
        return ValidationResult(
            stage=ValidationStage.SEMANTIC,
            passed=False,
            message=f"Missing required fields: {sorted(missing)}",
            details={"missing": sorted(missing)},
        )

    provenance = rule.get("provenance", "")
    if provenance not in _VALID_PROVENANCES:
        return ValidationResult(
            stage=ValidationStage.SEMANTIC,
            passed=False,
            message=(
                f"Invalid provenance {provenance!r}; must be one of {sorted(_VALID_PROVENANCES)}"
            ),
        )

    citations = rule.get("citations") or []
    if not citations:
        return ValidationResult(
            stage=ValidationStage.SEMANTIC,
            passed=False,
            message="citations must contain at least one entry",
        )

    for i, cit in enumerate(citations):
        if not isinstance(cit, dict) or "label" not in cit or "url" not in cit:
            return ValidationResult(
                stage=ValidationStage.SEMANTIC,
                passed=False,
                message=f"Citation {i} missing 'label' or 'url'",
                details={"citation_index": i, "citation": cit},
            )

    # D3: require at least one citation label that references a known legislative source.
    if not any(_LEGISLATIVE_REF_RE.search(cit.get("label", "")) for cit in citations):
        labels = [cit.get("label", "") for cit in citations]
        return ValidationResult(
            stage=ValidationStage.SEMANTIC,
            passed=False,
            message=(
                "No citation references a known legislative source. "
                "Include at least one label matching a statute (e.g. 'TCGA 1992 s.3'), "
                "Finance Act (e.g. 'FA 2004'), or HMRC manual reference "
                "(e.g. 'CG18000', 'IHTM12082', 'PTM011000')."
            ),
            details={"citation_labels": labels},
        )

    # D4: warn (non-blocking) about GOV.UK citations that return non-200 responses.
    stale_urls: list[str] = []
    url_check_log: list[dict[str, Any]] = []  # full per-URL diagnostics for CI debugging
    for cit in citations:
        url: str = cit.get("url", "")
        if not url.startswith("https://www.gov.uk"):
            continue
        entry: dict[str, Any] = {"url": url}
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
                entry["status"] = resp.status
                entry["final_url"] = resp.url  # resolved URL after any redirects
                if resp.status != 200:
                    stale_urls.append(url)
                    entry["stale"] = True
                else:
                    entry["stale"] = False
        except (urllib.error.URLError, OSError) as exc:
            entry["error"] = type(exc).__name__
            entry["error_detail"] = str(exc)
            entry["stale"] = True
            stale_urls.append(url)
        url_check_log.append(entry)

    if rule.get("monetary_output"):
        ast = rule.get("ast") or {}
        if not _final_result_is_rounded(ast):
            return ValidationResult(
                stage=ValidationStage.SEMANTIC,
                passed=False,
                message=(
                    "Rule declares monetary_output=true but the final result is not "
                    "wrapped in round(). Wrap the outermost expression in round(expr, 2) "
                    "per the rounding policy (docs/rules/rounding-policy.md)."
                ),
            )

    semantic_details: dict[str, Any] = {}
    if stale_urls:
        semantic_details["stale_urls"] = stale_urls
    if url_check_log:
        semantic_details["url_checks"] = url_check_log

    return ValidationResult(
        stage=ValidationStage.SEMANTIC,
        passed=True,
        message="All required fields present and valid",
        details=semantic_details,
    )


def _stage_canonicalisation(rule: dict[str, Any]) -> ValidationResult:
    """Stage 3: Recompile the DSL and verify both the stored AST and the
    recompiled AST produce the same checksum as the stored checksum."""
    dsl_source = rule.get("dsl_source", "")
    stored_checksum = rule.get("checksum", "")

    try:
        recompiled_ast = compile_dsl(dsl_source)
    except CompileError as exc:
        return ValidationResult(
            stage=ValidationStage.CANONICALISATION,
            passed=False,
            message=f"Recompilation failed: {exc}",
        )

    computed = _compute_ast_checksum(recompiled_ast)
    if computed != stored_checksum:
        return ValidationResult(
            stage=ValidationStage.CANONICALISATION,
            passed=False,
            message="Checksum mismatch: recompiled DSL checksum differs from stored checksum",
            details={"stored": stored_checksum, "computed": computed},
        )

    # Also verify the stored AST itself checksums to the same value.
    # A tampered AST could pass the DSL-recompile check above while diverging
    # from what the DSL actually describes.
    stored_ast = rule.get("ast")
    if stored_ast is not None:
        stored_ast_checksum = _compute_ast_checksum(stored_ast)
        if stored_ast_checksum != stored_checksum:
            return ValidationResult(
                stage=ValidationStage.CANONICALISATION,
                passed=False,
                message=(
                    "Stored AST checksum does not match stored checksum — "
                    "AST may have been tampered with independently of the DSL"
                ),
                details={
                    "stored_checksum": stored_checksum,
                    "stored_ast_checksum": stored_ast_checksum,
                },
            )

    return ValidationResult(
        stage=ValidationStage.CANONICALISATION,
        passed=True,
        message=f"Checksum verified: {computed[:16]}…",
        details={"checksum": computed},
    )


def _stage_execution(rule: dict[str, Any]) -> ValidationResult:
    """Stage 4: AST evaluates without runtime error on a zero-input smoke test."""
    ast = rule.get("ast")
    if not isinstance(ast, dict):
        return ValidationResult(
            stage=ValidationStage.EXECUTION,
            passed=False,
            message="ast field is not a dict",
        )

    # Smoke test with all-zero numeric variables — we only check it doesn't crash.
    # Real correctness is verified by worked examples in stage 5.
    try:
        evaluator = Evaluator(variables=_zero_variables(ast))
        evaluator.eval(ast)
    except EvaluationError as exc:
        return ValidationResult(
            stage=ValidationStage.EXECUTION,
            passed=False,
            message=f"Execution error on smoke-test inputs: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(
            stage=ValidationStage.EXECUTION,
            passed=False,
            message=f"Unexpected error during execution: {exc}",
        )

    return ValidationResult(
        stage=ValidationStage.EXECUTION,
        passed=True,
        message="Rule executes without error on smoke-test inputs",
    )


def _zero_variables(ast: dict[str, Any]) -> dict[str, Any]:
    """Walk the AST and collect all VAR names with safe smoke-test defaults."""
    variables: dict[str, Any] = {}

    def _remember(name: str, value: Any) -> None:
        if name not in variables:
            variables[name] = value

    def _walk(node: Any, bool_context: bool = False) -> None:
        if isinstance(node, dict):
            node_type = node.get("node")
            if node_type == "VAR":
                _remember(node["name"], False if bool_context else Decimal(0))
                return
            if node_type == "IF":
                _walk(node.get("cond"), bool_context=True)
                _walk(node.get("then"))
                _walk(node.get("else") or node.get("else_"))
                return
            if node_type in {"AND", "OR", "NOT"}:
                for item in node.get("args", []):
                    _walk(item, bool_context=True)
                return
            if node_type == "LET":
                for item in node.get("bindings", []):
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        _walk(item[1])
                _walk(node.get("body"))
                return
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item, bool_context=bool_context)

    _walk(ast)
    return variables


def _stage_worked_examples(
    rule: dict[str, Any],
    worked_examples: list[WorkedExample],
) -> ValidationResult:
    """Stage 5: Evaluate the AST against HMRC-published worked examples."""
    if not worked_examples:
        if _worked_examples_required(rule):
            return ValidationResult(
                stage=ValidationStage.WORKED_EXAMPLES,
                passed=False,
                message=(
                    "Worked examples are required for monetary or publication-ready rules; "
                    "no worked examples were provided or discovered for this rule"
                ),
            )
        return ValidationResult(
            stage=ValidationStage.WORKED_EXAMPLES,
            passed=True,
            message="No worked examples provided — stage skipped (pass)",
        )

    ast = rule.get("ast")
    if ast is None:
        return ValidationResult(
            stage=ValidationStage.WORKED_EXAMPLES,
            passed=False,
            message="Rule has no compiled AST — cannot run worked examples",
        )
    failures: list[dict[str, Any]] = []

    for ex in worked_examples:
        variables = {k: _to_decimal(v) if not isinstance(v, bool) else v
                     for k, v in ex.inputs.items()}
        try:
            result = Evaluator(variables=variables).eval(ast)
        except EvaluationError as exc:
            failures.append({
                "description": ex.description,
                "error": str(exc),
                "source": ex.source,
            })
            continue

        expected = _to_decimal(ex.expected) if not isinstance(ex.expected, bool) else ex.expected
        tolerance = Decimal(ex.tolerance)

        if isinstance(expected, bool):
            if result != expected:
                failures.append({
                    "description": ex.description,
                    "expected": expected,
                    "got": result,
                    "source": ex.source,
                })
        else:
            actual = _to_decimal(result)
            if abs(actual - expected) > tolerance:
                failures.append({
                    "description": ex.description,
                    "expected": str(expected),
                    "got": str(actual),
                    "tolerance": str(tolerance),
                    "source": ex.source,
                })

    if failures:
        return ValidationResult(
            stage=ValidationStage.WORKED_EXAMPLES,
            passed=False,
            message=f"{len(failures)} of {len(worked_examples)} worked example(s) failed",
            details={"failures": failures},
        )

    return ValidationResult(
        stage=ValidationStage.WORKED_EXAMPLES,
        passed=True,
        message=f"All {len(worked_examples)} worked example(s) passed",
    )


def _stage_human_review(rule: dict[str, Any]) -> ValidationResult:
    """Stage 6: Rule must carry a verifiable review record to be publication-ready.

    Accepts the new structured `review` block (reviewer, pr, approved_at) populated
    automatically by the CI pipeline, or falls back to the deprecated `reviewed_by`
    string for backwards compatibility with rules predating the new pipeline.
    """
    review = rule.get("review")
    if review and isinstance(review, dict):
        reviewer = review.get("reviewer", "")
        pr = review.get("pr")
        approved_at = review.get("approved_at")

        errors: list[str] = []
        if not reviewer:
            errors.append("reviewer (GitHub username or email)")
        if not isinstance(pr, int) or pr <= 0:
            errors.append("pr (positive integer GitHub PR number)")
        if not approved_at:
            errors.append("approved_at (ISO 8601 timestamp)")

        if errors:
            return ValidationResult(
                stage=ValidationStage.HUMAN_REVIEW,
                passed=False,
                message=f"review block is incomplete — missing or invalid: {', '.join(errors)}",
                details={"review": review},
            )

        if isinstance(approved_at, datetime):
            date_str = approved_at.strftime("%Y-%m-%d")
        else:
            date_str = str(approved_at)[:10]

        return ValidationResult(
            stage=ValidationStage.HUMAN_REVIEW,
            passed=True,
            message=f"Reviewed by @{reviewer} via PR #{pr} on {date_str}",
            details={"review": review},
        )

    # Backwards-compatible fallback: accept the deprecated reviewed_by string.
    reviewed_by = rule.get("reviewed_by")
    if reviewed_by:
        return ValidationResult(
            stage=ValidationStage.HUMAN_REVIEW,
            passed=True,
            message=f"Rule reviewed by: {reviewed_by}",
            details={
                "reviewed_by": reviewed_by,
                "migration": (
                    "reviewed_by is deprecated — the automated review pipeline now "
                    "populates a structured review: block with GitHub PR traceability"
                ),
            },
        )

    return ValidationResult(
        stage=ValidationStage.HUMAN_REVIEW,
        passed=False,
        message=(
            "Rule has not been reviewed — open a PR, get it approved by a designated "
            "reviewer, and the post-merge CI will populate the review: block automatically"
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_rule(
    rule_dict: dict[str, Any],
    worked_examples: list[WorkedExample] | None = None,
) -> list[ValidationResult]:
    """
    Run the full 6-stage validation pipeline on a rule dict.

    Args:
        rule_dict: The rule as a plain dict (as loaded from YAML or the MCP tool).
        worked_examples: Optional list of WorkedExample instances for stage 5.
            If omitted, examples are auto-loaded from `tests/worked_examples/`
            when the rule carries `tax_year`, `jurisdiction`, and `rule_id`.

    Returns:
        A list of six ValidationResult instances, one per stage, in order.
        If an early stage fails, remaining stages are returned as SKIPPED.
    """
    stages = [
        (_stage_syntax, ValidationStage.SYNTAX),
        (_stage_semantic, ValidationStage.SEMANTIC),
        (_stage_canonicalisation, ValidationStage.CANONICALISATION),
        (_stage_execution, ValidationStage.EXECUTION),
    ]

    results: list[ValidationResult] = []
    failed = False

    for fn, stage in stages:
        if failed:
            results.append(_skip(stage, "earlier stage failed"))
        else:
            r = fn(rule_dict)
            results.append(r)
            if not r.passed:
                failed = True

    # Stage 5
    if failed:
        results.append(_skip(ValidationStage.WORKED_EXAMPLES, "earlier stage failed"))
    else:
        examples = worked_examples
        if examples is None:
            examples_path = _default_worked_examples_path(rule_dict)
            examples = load_worked_examples(examples_path) if examples_path else []
        r5 = _stage_worked_examples(rule_dict, examples)
        results.append(r5)
        if not r5.passed:
            failed = True

    # Stage 6
    if failed:
        results.append(_skip(ValidationStage.HUMAN_REVIEW, "earlier stage failed"))
    else:
        results.append(_stage_human_review(rule_dict))

    return results


def load_worked_examples(yaml_path: Path) -> list[WorkedExample]:
    """
    Load worked examples from a YAML file.

    Expected structure:
        examples:
          - description: "Basic rate taxpayer"
            inputs:
              taxable_income: 30000
            expected: 3486
            tolerance: "0"  # optional
    """
    with yaml_path.open() as fh:
        data = yaml.safe_load(fh)
    examples = []
    for item in data.get("examples", []):
        examples.append(WorkedExample(
            description=item["description"],
            inputs=item["inputs"],
            expected=item["expected"],
            tolerance=str(item.get("tolerance", "0")),
            source=item.get("source"),
        ))
    return examples
