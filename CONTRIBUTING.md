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

Each rule lives in `src/hmrc_tax_mcp/registry/rules/<jurisdiction>/<tax_year>/<rule_id>.yaml`. Update the relevant fields:

- Change the numeric value(s) that the Budget altered.
- Bump the `version` field (semver patch: `1.0.0` → `1.0.1`, or minor: `1.0.0` → `1.1.0` for structural changes).
- Update `published_at` to today's ISO 8601 date.
- Add or update `citations` to reference the new HMRC source. At least one citation must carry a legislative reference (Act name, SI number, or HMRC manual code):
  ```yaml
  citations:
    - label: "ITEPA 2003 s.57 as amended by FA 2026 s.12"
      url: "https://www.legislation.gov.uk/ukpga/2026/..."
    - label: "HMRC — Income Tax rates and Personal Allowances"
      url: "https://www.gov.uk/income-tax-rates"
  ```
- Do **not** set `reviewed_by` or `review` — the post-merge CI populates the review block automatically after the PR is approved.

### Step 4 — Open the PR

- Title format: `rules: update <rule_ids> for <Budget/Finance Act name> (<date>)`
- Fill in the PR template (HMRC source URL, what changed, legislation reference). The template checklist forms part of the permanent audit trail.
- Link to the GitHub issue opened in Step 1.

CI will automatically run stages 1–5 of the validation pipeline and post a report as a PR comment. Fix any failures flagged in the comment before requesting review.

The PR must be reviewed, approved, and merged within **24 hours of HMRC publication** for time-sensitive changes (rate changes effective from the start of a new tax year must be published before 6 April).

### Step 5 — Reviewer approves (GitHub UI only)

The designated tax reviewer:

1. Reads the automated validation report comment (stages 1–5 pass/fail per rule).
2. Reviews the PR diff — exactly which values changed.
3. Opens the HMRC source URL from the PR description and confirms the values match.
4. Clicks **Approve** in the GitHub review UI. No terminal, no YAML editing required.

### Step 6 — Post-merge automation

After the PR is merged, CI automatically:

- Populates the `review:` block in each changed rule YAML (reviewer, PR number, approval timestamp, validation run ID).
- Prepends an entry to `RULES_CHANGELOG.md`.
- Commits both changes directly to `main` with `[skip ci]`.

No manual post-merge steps are needed.

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
# review: block is populated automatically by CI after PR approval — do not set manually
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
