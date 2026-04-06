"""Unit tests for UK tax year boundary utilities."""

from __future__ import annotations

import datetime

import pytest

from hmrc_tax_mcp.registry.tax_year import current_tax_year, tax_year_for_date


@pytest.mark.parametrize(
    "date_str, expected",
    [
        # Day before new year starts — still in previous year
        ("2025-04-05", "2024-25"),
        # First day of new year
        ("2025-04-06", "2025-26"),
        # Mid-year
        ("2025-10-01", "2025-26"),
        # Last day of tax year
        ("2026-04-05", "2025-26"),
        # First day of next year
        ("2026-04-06", "2026-27"),
        # 1 January — before April 6 so still prior year
        ("2026-01-01", "2025-26"),
        # Leap year — no special behaviour expected
        ("2024-02-29", "2023-24"),
        # Future year
        ("2030-04-06", "2030-31"),
        ("2030-04-05", "2029-30"),
    ],
)
def test_tax_year_for_date(date_str: str, expected: str) -> None:
    d = datetime.date.fromisoformat(date_str)
    assert tax_year_for_date(d) == expected


def test_current_tax_year_uses_today() -> None:
    """current_tax_year() must agree with tax_year_for_date(today)."""
    today = datetime.date.today()
    assert current_tax_year() == tax_year_for_date(today)


def test_current_tax_year_returns_string() -> None:
    result = current_tax_year()
    assert isinstance(result, str)
    # Format: YYYY-YY
    start, end = result.split("-")
    assert len(start) == 4
    assert len(end) == 2
    assert int(end) == (int(start) + 1) % 100


def test_current_tax_year_format_across_century_boundary() -> None:
    # 2099-00 would break naive implementations; ensure 2099-2100 → "2099-00"
    d = datetime.date(2099, 6, 1)
    result = tax_year_for_date(d)
    assert result == "2099-00"
