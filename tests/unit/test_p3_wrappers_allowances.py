"""Tests for P3 Bucket B wrappers and Bucket C/D allowances specialist rules."""

from __future__ import annotations

from decimal import Decimal

from hmrc_tax_mcp.evaluator import Evaluator
from hmrc_tax_mcp.registry.store import get_rule

ALL_YEARS = ["2025-26", "2026-27", "2027-28", "2028-29", "2029-30", "2030-31"]


def _eval(rule_id: str, inputs: dict, tax_year: str = "2025-26", jurisdiction: str = "rUK"):
    entry = get_rule(rule_id, jurisdiction=jurisdiction, tax_year=tax_year)
    assert entry is not None, f"Rule {rule_id!r} not found for {tax_year} ({jurisdiction})"
    converted = {k: (v if isinstance(v, bool) else Decimal(str(v))) for k, v in inputs.items()}
    return Evaluator(variables=converted).eval(entry.ast)


class TestInvestmentBondTimeApportionment:
    def test_8_of_10_uk_years(self):
        """£60k gain, 8/10 UK years → £48,000."""
        result = _eval(
            "investment_bond_time_apportionment",
            {"chargeable_gain": 60000, "uk_years": 8, "total_years": 10},
        )
        assert result == Decimal("48000.00")

    def test_all_uk_years(self):
        """All years UK resident — full gain returned."""
        result = _eval(
            "investment_bond_time_apportionment",
            {"chargeable_gain": 50000, "uk_years": 10, "total_years": 10},
        )
        assert result == Decimal("50000.00")

    def test_zero_total_years_returns_zero(self):
        result = _eval(
            "investment_bond_time_apportionment",
            {"chargeable_gain": 50000, "uk_years": 0, "total_years": 0},
        )
        assert result == Decimal("0.00")

    def test_all_years_same_checksum(self):
        base = get_rule(
            "investment_bond_time_apportionment", jurisdiction="rUK", tax_year="2025-26"
        )
        for yr in ALL_YEARS[1:]:
            e = get_rule(
                "investment_bond_time_apportionment", jurisdiction="rUK", tax_year=yr
            )
            assert e is not None and e.checksum == base.checksum


class TestRentARoomAllowance:
    def test_sole_letting_returns_7500(self):
        assert _eval("rent_a_room_allowance", {"shared_letting": False}) == Decimal("7500")

    def test_shared_letting_returns_3750(self):
        assert _eval("rent_a_room_allowance", {"shared_letting": True}) == Decimal("3750")

    def test_all_years_same_checksum(self):
        base = get_rule("rent_a_room_allowance", jurisdiction="rUK", tax_year="2025-26")
        for yr in ALL_YEARS[1:]:
            e = get_rule("rent_a_room_allowance", jurisdiction="rUK", tax_year=yr)
            assert e is not None and e.checksum == base.checksum


class TestTradingAllowance:
    def test_returns_1000(self):
        assert _eval("trading_allowance", {}) == Decimal("1000")

    def test_all_years_same_checksum(self):
        base = get_rule("trading_allowance", jurisdiction="rUK", tax_year="2025-26")
        for yr in ALL_YEARS[1:]:
            e = get_rule("trading_allowance", jurisdiction="rUK", tax_year=yr)
            assert e is not None and e.checksum == base.checksum


class TestPropertyAllowance:
    def test_returns_1000(self):
        assert _eval("property_allowance", {}) == Decimal("1000")

    def test_all_years_same_checksum(self):
        base = get_rule("property_allowance", jurisdiction="rUK", tax_year="2025-26")
        for yr in ALL_YEARS[1:]:
            e = get_rule("property_allowance", jurisdiction="rUK", tax_year=yr)
            assert e is not None and e.checksum == base.checksum


class TestNationalInsuranceQualifyingYears:
    def test_35_years_full_entitlement(self):
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 35})
        assert result == Decimal("1.0000")

    def test_40_years_still_full(self):
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 40})
        assert result == Decimal("1.0000")

    def test_20_years_partial(self):
        """20/35 = 0.5714 (rounded to 4dp)."""
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 20})
        assert result == Decimal("0.5714")

    def test_10_years_minimum(self):
        """10/35 = 0.2857."""
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 10})
        assert result == Decimal("0.2857")

    def test_under_10_years_zero(self):
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 9})
        assert result == Decimal("0.0000")

    def test_zero_years_zero(self):
        result = _eval("national_insurance_qualifying_years", {"qualifying_years": 0})
        assert result == Decimal("0.0000")

    def test_all_years_same_checksum(self):
        base = get_rule(
            "national_insurance_qualifying_years", jurisdiction="rUK", tax_year="2025-26"
        )
        for yr in ALL_YEARS[1:]:
            e = get_rule(
                "national_insurance_qualifying_years", jurisdiction="rUK", tax_year=yr
            )
            assert e is not None and e.checksum == base.checksum
