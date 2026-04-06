"""
UK tax year boundary utilities.

The UK tax year runs from 6 April (inclusive) to 5 April (inclusive) the
following calendar year.  For example:
  - 5 April 2025  → "2024-25"
  - 6 April 2025  → "2025-26"
  - 5 April 2026  → "2025-26"
  - 6 April 2026  → "2026-27"
"""

from __future__ import annotations

import datetime


def tax_year_for_date(d: datetime.date) -> str:
    """Return the UK tax year string (e.g. '2025-26') that contains *d*.

    The new tax year begins on 6 April each calendar year.
    """
    if d >= datetime.date(d.year, 4, 6):
        start = d.year
    else:
        start = d.year - 1
    end = start + 1
    return f"{start}-{str(end)[2:]}"


def current_tax_year() -> str:
    """Return the UK tax year string for today's date (e.g. '2025-26')."""
    return tax_year_for_date(datetime.date.today())
