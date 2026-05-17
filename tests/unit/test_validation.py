"""Tests for the 6-stage validation pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hmrc_tax_mcp.registry.store import get_rule, list_rules
from hmrc_tax_mcp.validation.pipeline import (
    ValidationStage,
    WorkedExample,
    load_worked_examples,
    validate_rule,
)

# ---------------------------------------------------------------------------
# Helpers for mocking the D4 GOV.UK URL check
# ---------------------------------------------------------------------------

def _mock_urlopen_200(url: str, timeout: int = 5) -> MagicMock:
    """Return a mock HTTP response with status 200."""
    resp = MagicMock()
    resp.status = 200
    resp.url = url
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_urlopen_404(url: str, timeout: int = 5) -> MagicMock:
    import urllib.error
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

WORKED_EXAMPLES_DIR = (
    Path(__file__).parent.parent / "worked_examples" / "2025-26" / "ruk"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule_dict(rule_id: str, jurisdiction: str = "rUK") -> dict[str, Any]:
    entry = get_rule(rule_id, jurisdiction=jurisdiction)
    assert entry is not None, f"Rule {rule_id!r} (jurisdiction={jurisdiction!r}) not found"
    return entry.model_dump()


def _run(rule_id: str, examples: list[WorkedExample] | None = None,
         jurisdiction: str = "rUK") -> list:
    return validate_rule(_rule_dict(rule_id, jurisdiction=jurisdiction), examples)


def _all_pass(results: list) -> bool:
    return all(r.passed for r in results)


# ---------------------------------------------------------------------------
# Stage 1 – Syntax
# ---------------------------------------------------------------------------

class TestStageSyntax:
    def test_valid_dsl_passes(self) -> None:
        results = _run("income_tax_bands")
        assert results[0].stage == ValidationStage.SYNTAX
        assert results[0].passed

    def test_empty_dsl_fails(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["dsl_source"] = ""
        results = validate_rule(rule)
        assert not results[0].passed

    def test_broken_dsl_fails(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["dsl_source"] = "let x = @@@ broken"
        results = validate_rule(rule)
        assert not results[0].passed

    def test_parse_error_is_reported_not_raised(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["dsl_source"] = "return if income > 1 then 2"
        results = validate_rule(rule)
        assert not results[0].passed
        assert "else" in results[0].message

    def test_later_stages_skipped_on_syntax_failure(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["dsl_source"] = "not valid dsl @@"
        results = validate_rule(rule)
        assert not results[0].passed
        # stages 2–6 should all be skipped
        for r in results[1:]:
            assert r.skipped, f"Expected skip, got: {r}"


# ---------------------------------------------------------------------------
# Stage 2 – Semantic
# ---------------------------------------------------------------------------

class TestStageSemantic:
    @pytest.fixture(autouse=True)
    def mock_govuk_urls_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unit tests must not make real HTTP calls — mock all GOV.UK URLs as reachable."""
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen_200)

    def test_valid_rule_passes(self) -> None:
        results = _run("cgt_exempt")
        assert results[1].passed

    def test_missing_field_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        del rule["description"]
        results = validate_rule(rule)
        assert not results[1].passed
        assert "description" in results[1].details.get("missing", [])

    def test_invalid_provenance_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["provenance"] = "guessed"
        results = validate_rule(rule)
        assert not results[1].passed
        assert "guessed" in results[1].message

    def test_empty_citations_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = []
        results = validate_rule(rule)
        assert not results[1].passed

    def test_malformed_citation_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = [{"label": "Missing URL"}]
        results = validate_rule(rule)
        assert not results[1].passed

    # D3: citation label quality
    def test_generic_only_citation_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = [{"label": "HMRC website", "url": "https://www.gov.uk"}]
        results = validate_rule(rule)
        assert not results[1].passed
        assert "legislative" in results[1].message.lower()

    def test_legislative_citation_passes(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = [
            {"label": "HMRC website", "url": "https://www.gov.uk"},
            {"label": "TCGA 1992 s.3", "url": "https://www.legislation.gov.uk/ukpga/1992/12"},
        ]
        results = validate_rule(rule)
        assert results[1].passed

    def test_hmrc_manual_ref_citation_passes(self) -> None:
        # CG-prefixed HMRC manual references should satisfy D3
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = [
            {"label": "CG18000", "url": "https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg18000"},
        ]
        results = validate_rule(rule)
        assert results[1].passed

    # D4: stale URL is now a blocking failure
    def test_stale_govuk_url_fails_stage2(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["citations"] = [
            {"label": "CG18000", "url": "https://www.gov.uk/this-page-does-not-exist-xyzzy-404"},
        ]
        # Override the class-level 200 mock: simulate a 404 for this specific test
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen_404):
            results = validate_rule(rule)
        # Stage 2 must fail — stale GOV.UK URLs are now blocking
        assert not results[1].passed
        stale = results[1].details.get("stale_urls", [])
        assert len(stale) == 1

    def test_non_govuk_url_not_checked(self) -> None:
        rule = _rule_dict("cgt_exempt")
        # Non-GOV.UK URL should not be checked at all — no stale_urls entry
        rule["citations"] = [
            {"label": "TCGA 1992", "url": "https://www.legislation.gov.uk/ukpga/1992/12"},
        ]
        results = validate_rule(rule)
        assert results[1].passed
        assert "stale_urls" not in results[1].details


# ---------------------------------------------------------------------------
# Stage 3 – Canonicalisation
# ---------------------------------------------------------------------------

class TestStageCanonicalisation:
    def test_valid_checksum_passes(self) -> None:
        results = _run("pa_taper")
        assert results[2].passed

    def test_tampered_checksum_fails(self) -> None:
        rule = _rule_dict("pa_taper")
        rule["checksum"] = "0" * 64
        results = validate_rule(rule)
        assert not results[2].passed
        assert "mismatch" in results[2].message.lower()

    def test_tampered_dsl_fails(self) -> None:
        rule = _rule_dict("income_tax_bands")
        # Add an extra band that changes the AST
        rule["dsl_source"] = rule["dsl_source"].replace("at 45%", "at 50%")
        results = validate_rule(rule)
        assert not results[2].passed

    def test_all_ruk_rules_have_valid_checksums(self) -> None:
        for entry in list_rules():
            rule = entry.model_dump()
            results = validate_rule(rule)
            assert results[2].passed, (
                f"Checksum invalid for {entry.rule_id}: {results[2].message}"
            )


# ---------------------------------------------------------------------------
# Stage 4 – Execution
# ---------------------------------------------------------------------------

class TestStageExecution:
    def test_income_tax_bands_executes(self) -> None:
        results = _run("income_tax_bands")
        assert results[3].passed

    def test_pa_taper_executes(self) -> None:
        results = _run("pa_taper")
        assert results[3].passed

    def test_all_ruk_rules_execute(self) -> None:
        for entry in list_rules():
            rule = entry.model_dump()
            results = validate_rule(rule)
            assert results[3].passed, (
                f"Execution failed for {entry.rule_id}: {results[3].message}"
            )

    def test_bad_ast_fails(self) -> None:
        rule = _rule_dict("cgt_exempt")
        rule["ast"] = {"node": "UNKNOWN_NODE_TYPE"}
        results = validate_rule(rule)
        assert not results[3].passed


# ---------------------------------------------------------------------------
# Stage 5 – Worked examples
# ---------------------------------------------------------------------------

class TestStageWorkedExamples:
    def test_no_examples_passes_trivially(self) -> None:
        results = _run("income_tax_bands", examples=[])
        assert results[4].passed
        assert "skipped" in results[4].message.lower()

    def test_correct_example_passes(self) -> None:
        ex = WorkedExample(
            description="30k basic rate",
            inputs={"taxable_income": 30000},
            expected=3486,
        )
        results = _run("income_tax_bands", examples=[ex])
        assert results[4].passed

    def test_wrong_expected_fails(self) -> None:
        ex = WorkedExample(
            description="Wrong expected",
            inputs={"taxable_income": 30000},
            expected=9999,
        )
        results = _run("income_tax_bands", examples=[ex])
        assert not results[4].passed
        assert results[4].details["failures"]

    def test_income_tax_bands_all_worked_examples(self) -> None:
        examples = load_worked_examples(
            WORKED_EXAMPLES_DIR / "income_tax_bands.yaml"
        )
        assert len(examples) == 6
        results = _run("income_tax_bands", examples=examples)
        assert results[4].passed, results[4].details

    def test_auto_loads_worked_examples_for_registry_rule(self) -> None:
        results = validate_rule(_rule_dict("income_tax_due"))
        assert results[4].passed, results[4].message

    def test_pa_taper_all_worked_examples(self) -> None:
        examples = load_worked_examples(WORKED_EXAMPLES_DIR / "pa_taper.yaml")
        results = _run("pa_taper", examples=examples)
        assert results[4].passed, results[4].details

    def test_cgt_rates_worked_examples(self) -> None:
        examples = load_worked_examples(WORKED_EXAMPLES_DIR / "cgt_rates.yaml")
        results = _run("cgt_rates", examples=examples)
        assert results[4].passed, results[4].details

    def test_pension_lsa_worked_examples(self) -> None:
        examples = load_worked_examples(WORKED_EXAMPLES_DIR / "pension_lsa.yaml")
        results = _run("pension_lsa", examples=examples)
        assert results[4].passed, results[4].details

    def test_state_pension_worked_examples(self) -> None:
        examples = load_worked_examples(
            WORKED_EXAMPLES_DIR / "state_pension_annual.yaml"
        )
        results = _run("state_pension_annual", examples=examples)
        assert results[4].passed, results[4].details

    def test_tolerance_allows_rounding(self) -> None:
        ex = WorkedExample(
            description="With tolerance",
            inputs={"taxable_income": 30000},
            expected=3486.01,
            tolerance="0.10",
        )
        results = _run("income_tax_bands", examples=[ex])
        assert results[4].passed


# ---------------------------------------------------------------------------
# Stage 6 – Human review
# ---------------------------------------------------------------------------

class TestStageHumanReview:
    def test_unreviewed_rule_fails(self) -> None:
        # All current rules have review=None and reviewed_by=None
        results = _run("income_tax_bands")
        assert not results[5].passed
        assert "not been reviewed" in results[5].message

    # ---- Deprecated reviewed_by path (backwards compatibility) ----

    def test_deprecated_reviewed_by_still_passes(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["reviewed_by"] = "tax-expert@example.com"
        ex = WorkedExample(
            description="Smoke test",
            inputs={"taxable_income": 30000},
            expected=3486,
        )
        results = validate_rule(rule, worked_examples=[ex])
        assert results[5].passed
        assert "tax-expert@example.com" in results[5].message
        # Deprecated path must carry a migration hint in details
        assert "migration" in results[5].details

    # ---- New structured review block ----

    def test_review_block_passes(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {
            "reviewer": "durbs182",
            "pr": 42,
            "approved_at": "2026-05-16T15:30:00Z",
        }
        ex = WorkedExample(
            description="Smoke test",
            inputs={"taxable_income": 30000},
            expected=3486,
        )
        results = validate_rule(rule, worked_examples=[ex])
        assert results[5].passed
        assert "@durbs182" in results[5].message
        assert "PR #42" in results[5].message

    def test_review_block_with_validation_run_passes(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {
            "reviewer": "durbs182",
            "pr": 99,
            "approved_at": "2026-05-16T15:30:00Z",
            "validation_run": "25965821214",
        }
        ex = WorkedExample(
            description="Smoke test",
            inputs={"taxable_income": 30000},
            expected=3486,
        )
        results = validate_rule(rule, worked_examples=[ex])
        assert results[5].passed
        assert results[5].details["review"]["validation_run"] == "25965821214"

    def test_review_block_missing_reviewer_fails(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {"reviewer": "", "pr": 42, "approved_at": "2026-05-16T15:30:00Z"}
        ex = WorkedExample(description="smoke", inputs={"taxable_income": 30000}, expected=3486)
        results = validate_rule(rule, worked_examples=[ex])
        assert not results[5].passed
        assert "reviewer" in results[5].message

    def test_review_block_missing_pr_fails(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {"reviewer": "durbs182", "approved_at": "2026-05-16T15:30:00Z"}
        ex = WorkedExample(description="smoke", inputs={"taxable_income": 30000}, expected=3486)
        results = validate_rule(rule, worked_examples=[ex])
        assert not results[5].passed
        assert "pr" in results[5].message.lower()

    def test_review_block_takes_precedence_over_reviewed_by(self) -> None:
        """review block is used even when deprecated reviewed_by is also present."""
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {
            "reviewer": "durbs182",
            "pr": 42,
            "approved_at": "2026-05-16T15:30:00Z",
        }
        rule["reviewed_by"] = "old-reviewer@example.com"
        ex = WorkedExample(description="smoke", inputs={"taxable_income": 30000}, expected=3486)
        results = validate_rule(rule, worked_examples=[ex])
        assert results[5].passed
        assert "@durbs182" in results[5].message
        assert "old-reviewer" not in results[5].message


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_all_stages_run_for_valid_rule(self) -> None:
        results = _run("cgt_exempt")
        assert len(results) == 6
        stages = [r.stage for r in results]
        expected_stages = list(ValidationStage)
        assert stages == expected_stages

    def test_full_pass_with_review_block_and_examples(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["review"] = {
            "reviewer": "auditor",
            "pr": 1,
            "approved_at": "2026-05-16T00:00:00Z",
        }
        examples = load_worked_examples(
            WORKED_EXAMPLES_DIR / "income_tax_bands.yaml"
        )
        results = validate_rule(rule, worked_examples=examples)
        assert _all_pass(results), [
            f"{r.stage}: {r.message}" for r in results if not r.passed
        ]

    def test_full_pass_with_deprecated_reviewed_by_and_examples(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["reviewed_by"] = "auditor@hmrc.example"
        examples = load_worked_examples(
            WORKED_EXAMPLES_DIR / "income_tax_bands.yaml"
        )
        results = validate_rule(rule, worked_examples=examples)
        assert _all_pass(results), [
            f"{r.stage}: {r.message}" for r in results if not r.passed
        ]

    def test_stored_ast_diverges_from_dsl_fails_stage3(self) -> None:
        """Stage 3 must fail when rule.ast was tampered but dsl_source is intact."""
        from hmrc_tax_mcp.ast.canonical import ast_checksum

        rule = _rule_dict("cgt_exempt")
        # Replace stored AST with a different valid AST (different constant value)
        tampered_ast = {"node": "CONST", "value": 9999}
        rule["ast"] = tampered_ast
        # Recompute checksum from tampered AST so the checksum itself is consistent,
        # but the stored AST no longer matches what DSL would compile to.
        rule["checksum"] = ast_checksum(tampered_ast)

        results = validate_rule(rule)
        # Stage 3 should detect that stored AST diverges from recompiled DSL AST
        assert not results[2].passed, "Stage 3 should fail when stored AST diverges from DSL"


# ---------------------------------------------------------------------------
# Rounding policy enforcement (monetary_output) — issue 2 from re-review
# ---------------------------------------------------------------------------

class TestRoundingPolicyEnforcement:
    """Stage 2 must fail if monetary_output=True but the final result is not round()."""

    def test_monetary_rule_without_round_fails_stage2(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["monetary_output"] = True
        # income_tax_bands AST is BAND_APPLY — final node is not round()
        results = validate_rule(rule)
        assert not results[1].passed
        assert "round" in results[1].message.lower()

    def test_monetary_rule_with_round_buried_not_final_fails(self) -> None:
        """round() inside a sub-expression but not wrapping the final result must fail."""
        rule = _rule_dict("income_tax_bands")
        rule["monetary_output"] = True
        # ADD(round(...), CONST) — round is NOT the final result
        rule["ast"] = {
            "node": "ADD",
            "args": [
                {"node": "CALL", "name": "round", "args": [
                    {"node": "CONST", "value": 1}, {"node": "CONST", "value": 2}
                ]},
                {"node": "CONST", "value": 100},
            ],
        }
        results = validate_rule(rule)
        assert not results[1].passed

    def test_non_monetary_rule_without_round_passes_stage2(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["monetary_output"] = False  # default
        results = validate_rule(rule)
        assert results[1].passed

    def test_monetary_rule_with_round_as_final_passes_stage2(self) -> None:
        rule = _rule_dict("income_tax_bands")
        rule["monetary_output"] = True
        rule["ast"] = {
            "node": "CALL",
            "name": "round",
            "args": [rule["ast"], {"node": "CONST", "value": 2}],
        }
        results = validate_rule(rule)
        assert results[1].passed, results[1].message


class TestWorkedExampleRequirement:
    def test_monetary_rule_without_examples_fails_stage5(self) -> None:
        rule = _rule_dict("income_tax_due")
        results = validate_rule(rule, worked_examples=[])
        assert not results[4].passed
        assert "worked examples are required" in results[4].message.lower()

    def test_monetary_let_with_round_body_passes(self) -> None:
        """LET whose body is round() should pass — LET body is the final value."""
        rule = _rule_dict("income_tax_bands")
        rule["monetary_output"] = True
        rule["ast"] = {
            "node": "LET",
            "bindings": [["tax", rule["ast"]]],
            "body": {"node": "CALL", "name": "round", "args": [
                {"node": "VAR", "name": "tax"},
                {"node": "CONST", "value": 2},
            ]},
        }
        results = validate_rule(rule)
        assert results[1].passed, results[1].message


# ---------------------------------------------------------------------------
# Frozen income tax threshold years (2027-28 through 2030-31)
# ---------------------------------------------------------------------------

FROZEN_YEARS = ["2027-28", "2028-29", "2029-30", "2030-31"]
FROZEN_YEARS_DIR = Path(__file__).parent.parent / "worked_examples"


class TestFrozenYearRegistry:
    """All frozen years must be stored as distinct registry entries."""

    def test_all_frozen_years_stored_distinctly(self) -> None:
        from hmrc_tax_mcp.registry.store import get_rule_snapshot

        for year in ["2025-26", "2026-27", *FROZEN_YEARS]:
            rules = get_rule_snapshot(year, "rUK")
            rule_ids = {r.rule_id for r in rules}
            assert "income_tax_bands" in rule_ids, (
                f"income_tax_bands missing from {year}/rUK snapshot"
            )

    def test_frozen_years_have_identical_checksum(self) -> None:
        from hmrc_tax_mcp.registry.store import get_rule_snapshot

        baseline = next(
            r for r in get_rule_snapshot("2025-26", "rUK")
            if r.rule_id == "income_tax_bands"
        )
        for year in FROZEN_YEARS:
            entry = next(
                r for r in get_rule_snapshot(year, "rUK")
                if r.rule_id == "income_tax_bands"
            )
            assert entry.checksum == baseline.checksum, (
                f"{year} income_tax_bands checksum differs from 2025-26 baseline"
            )
            assert entry.dsl_source == baseline.dsl_source, (
                f"{year} income_tax_bands DSL differs from 2025-26 baseline"
            )


class TestFrozenYearWorkedExamples:
    """Worked examples for each frozen year must pass validation."""

    def _run_year(self, tax_year: str) -> None:
        from hmrc_tax_mcp.registry.store import get_rule_snapshot

        rules = get_rule_snapshot(tax_year, "rUK")
        entry = next(r for r in rules if r.rule_id == "income_tax_bands")
        examples = load_worked_examples(
            FROZEN_YEARS_DIR / tax_year / "ruk" / "income_tax_bands.yaml"
        )
        assert len(examples) > 0, f"No worked examples found for {tax_year}"
        results = validate_rule(entry.model_dump(), worked_examples=examples)
        assert results[4].passed, (
            f"{tax_year} worked examples failed: {results[4].details}"
        )

    def test_2027_28_worked_examples(self) -> None:
        self._run_year("2027-28")

    def test_2028_29_worked_examples(self) -> None:
        self._run_year("2028-29")

    def test_2029_30_worked_examples(self) -> None:
        self._run_year("2029-30")

    def test_2030_31_worked_examples(self) -> None:
        self._run_year("2030-31")
