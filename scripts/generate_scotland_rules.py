"""
Generate Scotland copies of UK-wide rUK rules for 2026-27 and 2027-28.

Strategy:
  1. For each rUK rule that is UK-wide and not excluded, write a Scotland copy
     with only `jurisdiction` and `title` changed.
  2. Hand-craft Scotland-specific rules that cannot be copied from rUK.

Usage:
    python3.11 scripts/generate_scotland_rules.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hmrc_tax_mcp.ast.canonical import ast_checksum  # noqa: E402
from hmrc_tax_mcp.dsl.compiler import compile_dsl  # noqa: E402

RULES_DIR = Path(__file__).parent.parent / "src" / "hmrc_tax_mcp" / "registry" / "rules"

# These rUK rules must NOT be copied to Scotland — Scotland uses different rules
# or they are explicitly England/rUK/England-Wales-NI-only.
EXCLUDED_FROM_COPY: set[str] = {
    "sdlt_residential",                    # Scotland uses LBTT
    "sdlt_higher_rates",                   # Scotland uses Additional Dwelling Supplement
    "care_home_capital_threshold_england", # England-specific
    "income_tax_bands",                    # Scotland bands differ; hand-authored
    "income_tax_due",                      # Scotland has different band structure
    "is_higher_rate_taxpayer",             # Scotland higher rate starts at £43,662 not £50,270
    "property_income_bands",              # England/Wales/NI only; Scottish Parliament sets own rates  # noqa: E501
}


def _title_ruk_to_scotland(title: str) -> str:
    """Transform a rUK rule title to Scotland."""
    title = title.replace("(rUK ", "(Scotland ")
    title = title.replace(" rUK ", " Scotland ")
    return title


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=120)


def _generate_copies(tax_year: str) -> list[str]:
    """
    Copy UK-wide rUK rules to Scotland for the given tax_year.
    Returns list of rule IDs created.
    """
    ruk_dir = RULES_DIR / tax_year / "ruk"
    scotland_dir = RULES_DIR / tax_year / "scotland"

    # Collect existing Scotland rule IDs to avoid overwriting hand-authored ones
    existing_scotland = {p.stem for p in scotland_dir.glob("*.yaml")}

    created = []
    skipped_excluded = []
    skipped_existing = []

    for ruk_file in sorted(ruk_dir.glob("*.yaml")):
        rule_id = ruk_file.stem

        if rule_id in EXCLUDED_FROM_COPY:
            skipped_excluded.append(rule_id)
            continue

        if rule_id in existing_scotland:
            skipped_existing.append(rule_id)
            continue

        data = _load_yaml(ruk_file)

        # Build Scotland copy — only change jurisdiction and title
        scotland_data = dict(data)
        scotland_data["jurisdiction"] = "scotland"
        scotland_data["title"] = _title_ruk_to_scotland(str(data.get("title", "")))

        out_path = scotland_dir / ruk_file.name
        _write_yaml(out_path, scotland_data)
        created.append(rule_id)
        print(f"  [CREATED] {tax_year}/scotland/{ruk_file.name}")

    print(
        f"  [SKIPPED excluded]  ({len(skipped_excluded)}): {', '.join(skipped_excluded)}"
    )
    print(
        f"  [SKIPPED existing]  ({len(skipped_existing)}): {', '.join(sorted(skipped_existing))}"
    )

    return created


# ---------------------------------------------------------------------------
# Hand-crafted Scotland-specific rules
# ---------------------------------------------------------------------------

SCOTLAND_INCOME_TAX_BANDS_DSL = """\
bands taxable_income:
  0      to 12570  at 0%
  12570  to 16537  at 19%
  16537  to 29526  at 20%
  29526  to 43662  at 21%
  43662  to 75000  at 42%
  75000  to 125140 at 45%
  125140+          at 48%
"""

# Each band variable capped below at max(effective_pa, prior_band_upper) so the
# formula handles both the normal PA=12,570 case and the high-earner taper case
# where some "lost" PA income spills into the starter band.
SCOTLAND_INCOME_TAX_DUE_DSL = (
    "let effective_pa = taper adjusted_net_income:\n"
    "  threshold 100000\n"
    "  ratio 1 per 2\n"
    "  base 12570\n"
    "let starter_income = max(min(adjusted_net_income, 16537) - effective_pa, 0)\n"
    "let basic_income = max(min(adjusted_net_income, 29526) - max(effective_pa, 16537), 0)\n"
    "let intermediate_income ="
    " max(min(adjusted_net_income, 43662) - max(effective_pa, 29526), 0)\n"
    "let higher_income = max(min(adjusted_net_income, 75000) - max(effective_pa, 43662), 0)\n"
    "let advanced_income ="
    " max(min(adjusted_net_income, 125140) - max(effective_pa, 75000), 0)\n"
    "let top_income = max(adjusted_net_income - 125140, 0)\n"
    "return round("
    "starter_income * 0.19 + basic_income * 0.20 + intermediate_income * 0.21"
    " + higher_income * 0.42 + advanced_income * 0.45 + top_income * 0.48, 2)\n"
)

SCOTLAND_IS_HIGHER_RATE_DSL = "return adjusted_net_income > 43662\n"


def _tuples_to_lists(obj: object) -> object:
    """Recursively convert tuples to lists so PyYAML uses safe tags."""
    if isinstance(obj, tuple):
        return [_tuples_to_lists(item) for item in obj]
    if isinstance(obj, list):
        return [_tuples_to_lists(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    return obj


def _compile_and_checksum(dsl: str) -> tuple[dict, str]:
    ast = compile_dsl(dsl)
    checksum = ast_checksum(ast)
    # compile_dsl returns LET bindings as list-of-tuples; convert so yaml.dump
    # uses safe tags (lists → sequences; tuples → !!python/tuple which safe_load rejects)
    ast = _tuples_to_lists(ast)
    return ast, checksum


def _write_scotland_is_higher_rate_taxpayer(tax_year: str, published_at: str) -> None:
    rule_id = "is_higher_rate_taxpayer"
    out_path = RULES_DIR / tax_year / "scotland" / f"{rule_id}.yaml"
    if out_path.exists():
        print(f"  [EXISTS]  {tax_year}/scotland/{rule_id}.yaml — skipping")
        return

    ast, checksum = _compile_and_checksum(SCOTLAND_IS_HIGHER_RATE_DSL)

    data = {
        "rule_id": rule_id,
        "version": "1.0.0",
        "tax_year": tax_year,
        "jurisdiction": "scotland",
        "title": f"Higher-Rate Taxpayer Test (Scotland {tax_year})",
        "description": (
            "Returns true if the individual's adjusted net income exceeds the Scottish "
            "higher-rate threshold of £43,662. In Scotland the higher rate (42%) starts "
            "at £43,662 — lower than the rUK threshold of £50,270. Used to determine "
            "which Personal Savings Allowance tier applies (Scottish higher/advanced/top "
            "rate taxpayers receive £500; basic/starter-rate taxpayers receive £1,000). "
            "Input variable `adjusted_net_income` is gross income before the personal "
            "allowance deduction (as used in ITTOIA 2005 s.58).\n"
        ),
        "dsl_source": SCOTLAND_IS_HIGHER_RATE_DSL,
        "ast": ast,
        "checksum": checksum,
        "citations": [
            {
                "label": "HMRC – Scottish Income Tax",
                "url": "https://www.gov.uk/scottish-income-tax",
            },
            {
                "label": "Scotland Act 2016 s.13 – Scottish rate of income tax",
                "url": "https://www.legislation.gov.uk/ukpga/2016/11/section/13",
            },
        ],
        "provenance": "manual",
        "published_at": published_at,
        "reviewed_by": None,
    }
    _write_yaml(out_path, data)
    print(f"  [CREATED] {tax_year}/scotland/{rule_id}.yaml")


def _write_scotland_income_tax_bands(tax_year: str, published_at: str) -> None:
    rule_id = "income_tax_bands"
    out_path = RULES_DIR / tax_year / "scotland" / f"{rule_id}.yaml"
    if out_path.exists():
        print(f"  [EXISTS]  {tax_year}/scotland/{rule_id}.yaml — skipping")
        return

    ast, checksum = _compile_and_checksum(SCOTLAND_INCOME_TAX_BANDS_DSL)

    scot_gov_url = (
        "https://www.gov.scot/publications/scottish-income-tax-rates-and-bands"
        f"/pages/{tax_year.replace('-', '-to-')}/"
    )

    data = {
        "rule_id": rule_id,
        "version": "1.0.0",
        "tax_year": tax_year,
        "jurisdiction": "scotland",
        "title": f"Income Tax Bands (Scotland {tax_year})",
        "description": (
            f"Applies the {tax_year} Scottish income tax bands to non-savings, "
            "non-dividend income. Scotland operates six rates set by the Scottish "
            "Parliament: starter (19%), basic (20%), intermediate (21%), higher (42%), "
            "advanced (45%) and top (48%). The nil-rate band (0–£12,570) reflects "
            "the UK-wide personal allowance, so input variable `taxable_income` should "
            "be the gross amount before deduction of the personal allowance.\n"
        ),
        "dsl_source": SCOTLAND_INCOME_TAX_BANDS_DSL,
        "ast": ast,
        "checksum": checksum,
        "citations": [
            {
                "label": f"Scottish Government – Income Tax {tax_year} rates and bands",
                "url": scot_gov_url,
            },
            {
                "label": "HMRC – Scottish Income Tax",
                "url": "https://www.gov.uk/scottish-income-tax",
            },
            {
                "label": "Scotland Act 2016 s.13 – Scottish rate of income tax",
                "url": "https://www.legislation.gov.uk/ukpga/2016/11/section/13",
            },
        ],
        "provenance": "manual",
        "published_at": published_at,
        "reviewed_by": None,
    }
    _write_yaml(out_path, data)
    print(f"  [CREATED] {tax_year}/scotland/{rule_id}.yaml")


def _write_scotland_income_tax_due(tax_year: str, published_at: str) -> None:
    rule_id = "income_tax_due"
    out_path = RULES_DIR / tax_year / "scotland" / f"{rule_id}.yaml"
    if out_path.exists():
        print(f"  [EXISTS]  {tax_year}/scotland/{rule_id}.yaml — skipping")
        return

    ast, checksum = _compile_and_checksum(SCOTLAND_INCOME_TAX_DUE_DSL)

    scot_gov_url = (
        "https://www.gov.scot/publications/scottish-income-tax-rates-and-bands"
        f"/pages/{tax_year.replace('-', '-to-')}/"
    )

    if tax_year == "2027-28":
        desc = (
            "Calculates income tax on employment/trading income (non-savings, "
            "non-dividend) for a Scottish taxpayer in 2027-28, combining the personal "
            "allowance taper and the six Scottish rate bands. The Scottish bands are "
            "projected frozen at 2026-27 levels. Property income, savings, and dividends "
            "are taxed separately at UK rates — those rules are the UK-wide ones already "
            "copied for Scotland. The personal allowance (£12,570) tapers by £1 "
            "per £2 above £100,000, reaching £0 at £125,140. Result "
            "is rounded to the nearest penny. Input variable `adjusted_net_income` is "
            "gross taxable income before the personal allowance.\n"
        )
    else:
        desc = (
            "Calculates the total income tax liability for a Scottish taxpayer for a "
            "given adjusted net income, combining the personal allowance taper and the "
            "six Scottish rate bands in a single rule. The personal allowance "
            "(£12,570) tapers by £1 per £2 above £100,000, reaching "
            "£0 at £125,140. The starter_income formula handles both the normal "
            "case (PA=12,570, starter band covers 12,570–16,537) and the PA-taper "
            "case (PA<12,570, the lost PA income is taxed at the starter rate). Result is "
            "rounded to the nearest penny. Input variable `adjusted_net_income` is gross "
            "taxable income before the personal allowance.\n"
        )

    data = {
        "rule_id": rule_id,
        "version": "1.0.0",
        "tax_year": tax_year,
        "jurisdiction": "scotland",
        "title": f"Income Tax Due – Composite (Scotland {tax_year})",
        "description": desc,
        "dsl_source": SCOTLAND_INCOME_TAX_DUE_DSL,
        "ast": ast,
        "checksum": checksum,
        "citations": [
            {
                "label": "HMRC – Scottish Income Tax",
                "url": "https://www.gov.uk/scottish-income-tax",
            },
            {
                "label": f"Scottish Government – Income Tax {tax_year} rates and bands",
                "url": scot_gov_url,
            },
            {
                "label": "Scotland Act 2016 s.13 – Scottish rate of income tax",
                "url": "https://www.legislation.gov.uk/ukpga/2016/11/section/13",
            },
            {
                "label": "ITA 2007 s.35 – Deduction of personal allowance",
                "url": "https://www.legislation.gov.uk/ukpga/2007/3/section/35",
            },
        ],
        "provenance": "manual",
        "published_at": published_at,
        "reviewed_by": None,
        "monetary_output": True,
    }
    _write_yaml(out_path, data)
    print(f"  [CREATED] {tax_year}/scotland/{rule_id}.yaml")


def _copy_from_scotland_2627_to_2728(rule_id: str, published_at: str) -> None:
    """
    Copy a Scotland 2026-27 rule to 2027-28 with updated tax_year/published_at,
    used for Scotland-specific rules not present in rUK 2027-28.
    """
    src = RULES_DIR / "2026-27" / "scotland" / f"{rule_id}.yaml"
    dst = RULES_DIR / "2027-28" / "scotland" / f"{rule_id}.yaml"

    if dst.exists():
        print(f"  [EXISTS]  2027-28/scotland/{rule_id}.yaml — skipping")
        return

    if not src.exists():
        print(f"  [MISSING] 2026-27/scotland/{rule_id}.yaml — cannot copy")
        return

    data = _load_yaml(src)
    data["tax_year"] = "2027-28"
    data["published_at"] = published_at

    _write_yaml(dst, data)
    print(f"  [CREATED] 2027-28/scotland/{rule_id}.yaml  (copied from 2026-27)")


def main() -> None:
    print("=" * 70)
    print("Step 1 — Copying UK-wide rUK rules to Scotland (2026-27)")
    print("=" * 70)
    created_2627 = _generate_copies("2026-27")

    print()
    print("=" * 70)
    print("Step 2 — Copying UK-wide rUK rules to Scotland (2027-28)")
    print("=" * 70)
    created_2728 = _generate_copies("2027-28")

    print()
    print("=" * 70)
    print("Step 3 — Hand-crafting Scotland-specific rules (2026-27)")
    print("=" * 70)
    _write_scotland_is_higher_rate_taxpayer("2026-27", "2026-04-06T00:00:00Z")
    _write_scotland_income_tax_due("2026-27", "2026-04-06T00:00:00Z")
    # income_tax_bands already exists for 2026-27 — will report EXISTS

    print()
    print("=" * 70)
    print("Step 4 — Hand-crafting Scotland-specific rules (2027-28)")
    print("=" * 70)
    _write_scotland_income_tax_bands("2027-28", "2027-04-06T00:00:00Z")
    _write_scotland_income_tax_due("2027-28", "2027-04-06T00:00:00Z")
    _write_scotland_is_higher_rate_taxpayer("2027-28", "2027-04-06T00:00:00Z")

    # Check for Scotland-specific rules from 2026-27 not in rUK 2027-28
    print()
    print("Step 5 — Checking Scotland-specific 2026-27 rules missing from 2027-28 Scotland")
    ruk_2728_ids = {p.stem for p in (RULES_DIR / "2027-28" / "ruk").glob("*.yaml")}
    scotland_2627_ids = {p.stem for p in (RULES_DIR / "2026-27" / "scotland").glob("*.yaml")}

    # Rules that are in Scotland 2026-27 but not in rUK 2027-28 need to be copied
    # (the generation script handles rules that ARE in rUK 2027-28)
    candidates_to_copy = scotland_2627_ids - ruk_2728_ids - EXCLUDED_FROM_COPY
    # Also exclude rules we're already hand-crafting for 2027-28
    already_handled = {"income_tax_bands", "income_tax_due", "is_higher_rate_taxpayer"}
    candidates_to_copy -= already_handled

    print(
        "  Scotland 2026-27 rules not in rUK 2027-28 (excluding excluded+handled): "
        f"{sorted(candidates_to_copy)}"
    )
    for rule_id in sorted(candidates_to_copy):
        _copy_from_scotland_2627_to_2728(rule_id, "2027-04-06T00:00:00Z")

    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"  2026-27 copies created: {len(created_2627)}")
    print(f"  2027-28 copies created: {len(created_2728)}")

    # Final count
    s2627 = list((RULES_DIR / "2026-27" / "scotland").glob("*.yaml"))
    s2728 = list((RULES_DIR / "2027-28" / "scotland").glob("*.yaml"))
    print(f"  Scotland 2026-27 total: {len(s2627)} rules")
    print(f"  Scotland 2027-28 total: {len(s2728)} rules")


if __name__ == "__main__":
    main()
