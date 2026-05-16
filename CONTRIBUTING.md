# Contributing to uk-tax-mcp

Thank you for contributing. This guide covers the two main contribution paths: general code changes and — most critically — **rule updates triggered by Budget announcements or HMRC policy changes**.

---

## General contributions

- Open an issue before starting non-trivial work.
- Fork the repository and create a branch from `main`.
- Run the test suite and linter before submitting a PR:
  ```bash
  pip install -e ".[dev,server]"
  ruff check src tests
  mypy src
  pytest
  ```
- Follow the code style and conventions already present in the codebase.
- Keep changes focused — one logical change per PR.

---

## Budget change process

UK tax rules change with every Budget, Finance Act, and HMRC policy update. The goal is to publish updated rules **within 24 hours of HMRC publication**. The steps below make that achievable without heroics.

### Step 1 — Detect the announcement

Monitor HMRC publications through the following channels:

- **GOV.UK Budget & spending review collections:** https://www.gov.uk/government/collections/budgets
- **HMRC email updates:** subscribe at https://www.gov.uk/email-signup (select "Budget and tax", "HMRC news", and relevant policy areas)
- **HMRC What's New:** https://www.gov.uk/government/collections/hm-revenue-customs-whats-new
- **legislation.gov.uk:** https://www.legislation.gov.uk/uksi (statutory instruments) and https://www.legislation.gov.uk/ukpga (primary legislation)

When a relevant announcement is detected, open a GitHub issue immediately using the title format:

```
Budget/policy update: <event name> — <date> — rule update required
```

List every `rule_id` that may be affected. Err on the side of over-listing — it is easier to close unaffected rules than to miss one.

### Step 2 — Identify affected rules

For each changed threshold, rate, or limit:

1. Search `rules/` for YAML files containing the old numeric value:
   ```bash
   grep -r "12570" rules/   # e.g. to find personal allowance rules
   ```
2. Cross-reference the rule's `citations` field against the HMRC source that changed.
3. Note the affected `rule_id`, `tax_year`, and `jurisdiction` for each file.

### Step 3 — Draft the rule update

Each rule lives in `rules/<jurisdiction>/<tax_year>/<rule_id>.yaml`. Update the relevant fields:

- Change the numeric value(s) that the Budget altered.
- Bump the `version` field (semver patch: `1.0.0` → `1.0.1`, or minor: `1.0.0` → `1.1.0` for structural changes).
- Update `published_at` to today's ISO 8601 date.
- Add or update `citations` to reference the new HMRC source:
  ```yaml
  citations:
    - label: "ITEPA 2003 s.57 as amended by FA 2026 s.12"
      url: "https://www.legislation.gov.uk/ukpga/2026/..."
    - label: "HMRC — Income Tax rates and Personal Allowances"
      url: "https://www.gov.uk/income-tax-rates"
  ```
- Leave `reviewed_by: null` — the review gate is enforced by the validation pipeline.

### Step 4 — Validate the change

Run the full validation pipeline against each updated rule:

```bash
# Via the MCP server directly
python -c "
import asyncio
from hmrc_tax_mcp.server import handle_call_tool
result = asyncio.run(handle_call_tool('validate_rule', {
    'rule_id': 'income_tax_bands',
    'tax_year': '2026-27',
    'jurisdiction': 'rUK',
}))
print(result[0].text)
"

# Or run the full test suite (integration tests exercise rule execution)
pytest tests/
```

All 6 validation stages must pass before submitting the PR. Fix any failures before proceeding.

### Step 5 — Update RULES_CHANGELOG.md

Add an entry at the top of `RULES_CHANGELOG.md` using the defined format:

```markdown
## YYYY-MM-DD — <Budget or Finance Act name>

| Field | Value |
|---|---|
| **Budget / Finance Act date** | YYYY-MM-DD |
| **HMRC publication** | e.g. Spring Statement 2026 |
| **Affected rule IDs** | `income_tax_bands.2026-27`, `pa.taper.2026-27` |
| **Jurisdictions** | rUK |
| **What changed** | Personal allowance frozen at £12,570 for 2026-27; basic rate limit increased from £37,700 to £38,200 per Finance Act 2026. |
| **HMRC source** | https://www.gov.uk/... |
| **Legislation reference** | ITA 2007 s.10, FA 2026 s.5 |
| **Updated by** | your-github-handle |
| **PR** | #<PR number> |
```

### Step 6 — Submit the PR

- Title format: `rules: update <rule_ids> for <Budget/Finance Act name> (<date>)`
- Link to the GitHub issue opened in Step 1.
- Include a brief description of what changed and the HMRC source.
- Assign to a reviewer with tax domain knowledge.

The PR must be reviewed, approved, and merged within **24 hours of HMRC publication** for time-sensitive changes (rate changes effective from the start of a new tax year must be published before 6 April).

### Step 7 — Set reviewed_by after merge

After the PR is reviewed and the reviewer confirms the values match the HMRC source, update the rule YAML to set `reviewed_by` to the reviewer's name or email. This lifts the publication block and marks the rule as production-ready.

---

## Rule YAML format reference

```yaml
rule_id: income_tax_bands
version: 1.0.0
title: "Income Tax Bands — rUK 2026-27"
description: "Computes income tax liability from taxable income using the 2026-27 rUK bands."
tax_year: "2026-27"
jurisdiction: rUK
published_at: "2026-05-16"
provenance: manual
reviewed_by: null          # set after human review; null blocks publication
citations:
  - label: "ITA 2007 s.10"
    url: "https://www.legislation.gov.uk/ukpga/2007/3/section/10"
  - label: "HMRC — Income Tax rates and Personal Allowances"
    url: "https://www.gov.uk/income-tax-rates"
dsl: |
  # DSL source here
ast: {}                    # populated by the compiler; do not edit manually
checksum: ""               # populated by the compiler; do not edit manually
```

---

## Disclaimer

All outputs from this engine are **deterministic calculations, not professional tax advice**. Contributors are responsible for ensuring that rule values accurately reflect the cited HMRC source at the time of publication. Incorrect values in published rules are a product defect — treat them with the same urgency as a security vulnerability.
