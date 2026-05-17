#!/usr/bin/env python3
"""Validate changed rule YAML files for stages 1–5 of the pipeline.

Stage 6 (Human Review) is intentionally skipped — it is satisfied by the
GitHub PR approval that triggers the post-merge populate workflow.

Usage:
    python scripts/validate_changed_rules.py [file1.yaml file2.yaml ...]

    With no arguments: reads newline-separated paths from stdin.

Output:
    Markdown validation report written to stdout.

Exit code:
    0  all changed rules pass stages 1–5
    1  one or more rules fail stages 1–5
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hmrc_tax_mcp.validation.pipeline import validate_rule  # noqa: E402

_STAGE_HEADERS = ["Syntax", "Semantic", "Canonical", "Execution", "Worked Examples"]


def _icon(passed: bool, skipped: bool) -> str:
    if skipped:
        return "⏭️"
    return "✅" if passed else "❌"


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = [Path(p.strip()) for p in sys.stdin if p.strip()]

    existing = [p for p in paths if p.exists()]
    if not existing:
        print("No changed rule files detected.")
        sys.exit(0)

    rows: list[list[str]] = []
    warnings: list[str] = []
    any_failed = False

    for path in existing:
        try:
            rule_dict = yaml.safe_load(path.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"::error::Failed to load {path}: {exc}", file=sys.stderr)
            any_failed = True
            continue

        results = validate_rule(rule_dict)
        stages_1_to_5 = results[:5]

        failed = any(not r.passed and not r.skipped for r in stages_1_to_5)
        if failed:
            any_failed = True

        # Emit per-URL check results as debug annotations (visible when step debug is on).
        # Stale URLs are now a stage-2 failure, so any_failed already covers them.
        if len(results) > 1:
            stage2 = results[1]
            rule_id = rule_dict.get("rule_id", path.stem)
            for entry in stage2.details.get("url_checks", []):
                if "error" in entry:
                    print(
                        f"::debug::D4 url_check [{rule_id}] {entry['url']} → "
                        f"{entry['error']}: {entry['error_detail']}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"::debug::D4 url_check [{rule_id}] {entry['url']} → "
                        f"HTTP {entry['status']} (final: {entry.get('final_url', entry['url'])})",
                        file=sys.stderr,
                    )

        rule_id = rule_dict.get("rule_id", path.stem)
        tax_year = rule_dict.get("tax_year", "?")
        jurisdiction = rule_dict.get("jurisdiction", "?")
        stage_icons = [_icon(r.passed, r.skipped) for r in stages_1_to_5]
        rows.append([f"`{rule_id}`", tax_year, jurisdiction, *stage_icons])

    header = "| Rule | Tax Year | Jurisdiction | " + " | ".join(_STAGE_HEADERS) + " |"
    divider = "|---|---|---|" + "|".join(["---"] * len(_STAGE_HEADERS)) + "|"

    print("## Rule Validation Report")
    print()
    print(header)
    print(divider)
    for row in rows:
        print("| " + " | ".join(row) + " |")

    if warnings:
        print()
        print("⚠️ **Warnings** (non-blocking):")
        for w in warnings:
            print(f"- {w}")

    print()
    print(
        "> Stage 6 (Human Review) is intentionally pending — "
        "it is satisfied by PR approval."
    )

    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
