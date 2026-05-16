"""
Integration tests for the MCP tool handlers.

Calls handle_call_tool() against the live registry, exercising the full path:
  tool name → registry lookup → AST evaluation → JSON response.

These are integration tests (not unit tests) because they depend on the real
YAML rules on disk. They run fast (no network, no external services) but do
exercise the full stack.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from hmrc_tax_mcp import server


def _call(name: str, arguments: dict) -> dict:
    result = asyncio.run(server.handle_call_tool(name, arguments))
    assert len(result) == 1
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# list_rules
# ---------------------------------------------------------------------------

class TestListRules:
    def test_returns_non_empty_list(self) -> None:
        data = _call("list_rules", {})
        assert isinstance(data, list)
        assert len(data) > 0

    def test_each_entry_has_required_fields(self) -> None:
        data = _call("list_rules", {})
        for entry in data:
            assert "rule_id" in entry
            assert "version" in entry
            assert "tax_year" in entry
            assert "jurisdiction" in entry

    def test_income_tax_bands_present(self) -> None:
        data = _call("list_rules", {})
        ids = [e["rule_id"] for e in data]
        assert "income_tax_bands" in ids


# ---------------------------------------------------------------------------
# get_rule
# ---------------------------------------------------------------------------

class TestGetRule:
    def test_known_rule_returned(self) -> None:
        data = _call(
            "get_rule",
            {"rule_id": "income_tax_bands", "jurisdiction": "rUK", "tax_year": "2025-26"},
        )
        assert data["rule_id"] == "income_tax_bands"
        assert data["jurisdiction"] == "rUK"
        assert data["tax_year"] == "2025-26"

    def test_rule_contains_ast_and_checksum(self) -> None:
        data = _call(
            "get_rule",
            {"rule_id": "pa_taper", "jurisdiction": "rUK", "tax_year": "2025-26"},
        )
        assert "ast" in data
        assert "checksum" in data
        assert len(data["checksum"]) == 64  # SHA-256 hex

    def test_unknown_rule_returns_error(self) -> None:
        data = _call("get_rule", {"rule_id": "nonexistent_rule_xyz"})
        assert "error" in data

    def test_latest_version_resolved(self) -> None:
        data = _call(
            "get_rule",
            {"rule_id": "income_tax_bands", "jurisdiction": "rUK", "tax_year": "2025-26"},
        )
        assert "version" in data


# ---------------------------------------------------------------------------
# execute_rule — rule execution integration
# ---------------------------------------------------------------------------

class TestExecuteRule:
    """
    Execute real registry rules and verify outputs against known HMRC values.
    """

    def test_income_tax_bands_basic_rate_only(self) -> None:
        # taxable_income = £20,000 (post-PA)
        # Band 12570–50270 @ 20%: (20000-12570) × 0.20 = 7430 × 0.20 = 1486
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"taxable_income": 20000},
            },
        )
        assert "output" in data
        assert Decimal(str(data["output"])) == Decimal("1486")

    def test_income_tax_bands_crosses_higher_rate(self) -> None:
        # taxable_income = £60,000
        # Basic: (50270-12570) × 0.20 = 37700 × 0.20 = 7540
        # Higher: (60000-50270) × 0.40 = 9730 × 0.40 = 3892
        # Total: 11432
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"taxable_income": 60000},
            },
        )
        assert Decimal(str(data["output"])) == Decimal("11432")

    def test_income_tax_bands_nil_income(self) -> None:
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"taxable_income": 0},
            },
        )
        assert Decimal(str(data["output"])) == Decimal("0")

    def test_pa_taper_below_threshold_full_allowance(self) -> None:
        # adjusted_net_income = £80,000 — below £100,000 threshold
        data = _call(
            "execute_rule",
            {
                "rule_id": "pa_taper",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"adjusted_net_income": 80000},
            },
        )
        assert Decimal(str(data["output"])) == Decimal("12570")

    def test_pa_taper_partially_tapered(self) -> None:
        # adjusted_net_income = £110,000
        # excess = £10,000; reduction = £5,000; allowance = £7,570
        data = _call(
            "execute_rule",
            {
                "rule_id": "pa_taper",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"adjusted_net_income": 110000},
            },
        )
        assert Decimal(str(data["output"])) == Decimal("7570")

    def test_pa_taper_fully_tapered(self) -> None:
        # adjusted_net_income = £125,140 → allowance = £0
        data = _call(
            "execute_rule",
            {
                "rule_id": "pa_taper",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"adjusted_net_income": 125140},
            },
        )
        assert Decimal(str(data["output"])) == Decimal("0")

    def test_execute_with_trace_flag(self) -> None:
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"taxable_income": 20000},
                "trace": True,
            },
        )
        assert "trace" in data
        assert isinstance(data["trace"], list)
        assert len(data["trace"]) > 0

    def test_execute_returns_rule_id_and_checksum(self) -> None:
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {"taxable_income": 20000},
            },
        )
        assert data["rule_id"] == "income_tax_bands"
        assert "checksum" in data

    def test_missing_input_variable_returns_error(self) -> None:
        data = _call(
            "execute_rule",
            {
                "rule_id": "income_tax_bands",
                "jurisdiction": "rUK",
                "tax_year": "2025-26",
                "inputs": {},  # missing taxable_income
            },
        )
        assert "error" in data

    def test_unknown_rule_returns_error(self) -> None:
        data = _call(
            "execute_rule",
            {"rule_id": "does_not_exist", "inputs": {"x": 1}},
        )
        assert "error" in data


# ---------------------------------------------------------------------------
# Rule upload pipeline: compile_dsl → validate_rule
# ---------------------------------------------------------------------------

class TestRuleUploadPipeline:
    """
    Exercises the two-step path a new rule takes before being added to the
    registry: DSL compilation (compile_dsl) then full validation (validate_rule).
    """

    _VALID_DSL = "bands taxable_income:\n  12570 to 50270 at 20%\n  50270+ at 40%\n"

    def test_compile_dsl_returns_ast_and_checksum(self) -> None:
        data = _call("compile_dsl", {"dsl": self._VALID_DSL})
        assert "ast" in data
        assert "checksum" in data
        assert isinstance(data["ast"], dict)
        assert len(data["checksum"]) == 64

    def test_compile_dsl_invalid_syntax_returns_error(self) -> None:
        data = _call("compile_dsl", {"dsl": "return if income > 1 then 2"})
        assert "error" in data

    def test_compile_dsl_empty_source_returns_error(self) -> None:
        data = _call("compile_dsl", {"dsl": ""})
        assert "error" in data

    def test_compile_dsl_taper_expression(self) -> None:
        dsl = "taper adjusted_net_income:\n  threshold 100000\n  ratio 1 per 2\n  base 12570\n"
        data = _call("compile_dsl", {"dsl": dsl})
        assert "ast" in data
        assert data["ast"]["node"] == "TAPER"

    def test_validate_rule_passes_stages_1_to_4(self) -> None:
        # income_tax_bands is a well-formed rule; stages 1-4 must all pass
        data = _call(
            "validate_rule",
            {"rule_id": "income_tax_bands", "jurisdiction": "rUK", "tax_year": "2025-26"},
        )
        assert "stages" in data
        stage_names = [s["stage"] for s in data["stages"]]
        assert "syntax" in stage_names
        assert "semantic" in stage_names
        assert "canonicalisation" in stage_names
        assert "execution" in stage_names

        for stage in data["stages"]:
            if stage["stage"] in {"syntax", "semantic", "canonicalisation", "execution"}:
                assert stage["passed"] is True, (
                    f"Stage {stage['stage']} failed: {stage['message']}"
                )

    def test_validate_rule_pa_taper_passes(self) -> None:
        data = _call(
            "validate_rule",
            {"rule_id": "pa_taper", "jurisdiction": "rUK", "tax_year": "2025-26"},
        )
        passed_stages = {s["stage"] for s in data["stages"] if s["passed"]}
        assert "syntax" in passed_stages
        assert "execution" in passed_stages

    def test_validate_rule_unknown_rule_returns_error(self) -> None:
        data = _call("validate_rule", {"rule_id": "made_up_rule_xyz"})
        assert "error" in data


# ---------------------------------------------------------------------------
# tax_get_rule_snapshot
# ---------------------------------------------------------------------------

class TestRuleSnapshot:
    def test_snapshot_returns_rules_for_year(self) -> None:
        data = _call(
            "tax_get_rule_snapshot",
            {"tax_year": "2025-26", "jurisdiction": "rUK"},
        )
        assert data["tax_year"] == "2025-26"
        assert data["jurisdiction"] == "rUK"
        assert isinstance(data["rules"], list)
        assert len(data["rules"]) > 0

    def test_snapshot_all_entries_match_year_and_jurisdiction(self) -> None:
        data = _call(
            "tax_get_rule_snapshot",
            {"tax_year": "2025-26", "jurisdiction": "rUK"},
        )
        for rule in data["rules"]:
            assert rule["tax_year"] == "2025-26"
            assert rule["jurisdiction"] == "rUK"

    def test_snapshot_unknown_year_returns_empty(self) -> None:
        data = _call(
            "tax_get_rule_snapshot",
            {"tax_year": "1900-01", "jurisdiction": "rUK"},
        )
        assert data["rules"] == []
